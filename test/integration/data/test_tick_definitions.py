"""Definition projection on a migrated tick database (slice 224; FR7, TD5).

Day files are the definition fixture's record with hand-set windows and terms,
under a header spanning the unit's UTC day; units are seeded at *verified*.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.dbn_files import definition_file_bytes
from tick_support.fake_provider import FakeClock, FakeTickProvider
from tick_support.runs import connect, tick_run

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    CME_DATASET,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.definitions import project_definitions
from manta_trading.data.tick.manifest_reads import units_of_request
from manta_trading.data.tick.run_context import TickRun

AConn = psycopg.AsyncConnection[Any]
START = datetime(2026, 9, 29, 12, tzinfo=UTC)
READER = DbnFileReader()
NS_PER_DAY = 86_400 * 1_000_000_000
ACTIVATION = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp()) * 1_000_000_000
EXPIRATION = ACTIVATION + 90 * NS_PER_DAY
UNDEF_TS = 2**64 - 1
UNDEF_INT64 = 2**63 - 1

ESZ4: dict[str, Any] = {
    "instrument_id": 42_035_063,
    "raw_symbol": b"ESZ4",
    "asset": b"ES",
    "exchange": b"XCME",
    "instrument_class": b"F",
    "security_type": b"FUT",
    "cfi": b"FFIXSX",
    "currency": b"USD",
    "activation": ACTIVATION,
    "expiration": EXPIRATION,
    "min_price_increment": 250_000_000,
    "display_factor": 1_000_000_000,
    "unit_of_measure": b"IPNT",
    "unit_of_measure_qty": 50_000_000_000,
    "contract_multiplier": 50,
}
SPREAD: dict[str, Any] = ESZ4 | {
    "instrument_id": 42_004_904,
    "raw_symbol": b"ESH6-ESU6",
    "asset": b"",  # undefined → NULL
    "min_price_increment": UNDEF_INT64,  # undefined → NULL
}
DAY1, DAY2, DAY3 = date(2024, 9, 3), date(2024, 9, 4), date(2024, 9, 5)


@pytest.fixture
async def conn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with connect(migrated_tick_db) as connection:
        yield connection


@pytest.fixture
def run(conn: AConn, tmp_path: Path) -> TickRun:
    clock = FakeClock(START)
    return tick_run(conn, FakeTickProvider(clock=clock), tmp_path, clock)


async def _unit(
    run: TickRun,
    day: date,
    records: list[dict[str, Any]],
    *,
    schema: TickSchema = TickSchema.DEFINITION,
) -> int:
    """A *verified* unit whose file holds ``records`` on ``day``."""
    content = definition_file_bytes(CME_DATASET, day, SType.PARENT, records)
    name = f"glbx-mdp3-{day:%Y%m%d}.{schema.value}.dbn.zst"
    (run.archive_root / "JOB").mkdir(exist_ok=True)
    (run.archive_root / "JOB" / name).write_bytes(content)
    cursor = await run.conn.execute(
        "INSERT INTO tick_request (dataset, schema, symbols, stype_in, range_start,"
        " range_end, delivery_mode, is_adopted, estimated_cost_usd, requested_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'batch_job', FALSE, 0, %s)"
        " RETURNING request_id",
        (
            CME_DATASET,
            schema.value,
            ["ES.FUT"],
            SType.PARENT.value,
            day,
            day + timedelta(days=1),
            START,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    cursor = await run.conn.execute(
        "INSERT INTO tick_archive_unit (request_id, unit_date, state,"
        " state_changed_at, fetch_status, attempt_count, file_path,"
        " file_size_bytes, file_sha256) VALUES (%s, %s, 'verified', %s, 'UNKNOWN',"
        " 0, %s, %s, %s) RETURNING unit_id",
        (
            row[0],
            day,
            START,
            f"JOB/{name}",
            len(content),
            hashlib.sha256(content).hexdigest(),
        ),
    )
    unit = await cursor.fetchone()
    assert unit is not None
    return int(unit[0])


async def _status(
    run: TickRun, unit_id: int
) -> tuple[UnitState, FetchStatus, str | None, int | None]:
    cursor = await run.conn.execute(
        "SELECT state, fetch_status, failure_reason, decoded_record_count"
        " FROM tick_archive_unit WHERE unit_id = %s",
        (unit_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    return UnitState(row[0]), FetchStatus(row[1]), row[2], row[3]


async def _count(run: TickRun) -> int:
    cursor = await run.conn.execute("SELECT count(*) FROM tick_definition")
    row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def test_new_instruments_are_inserted_and_the_unit_is_ingested(
    run: TickRun,
) -> None:
    unit = await _unit(run, DAY1, [ESZ4, SPREAD])
    tally = await project_definitions(run, READER)
    assert (tally.projected, tally.inserted, tally.noops, tally.failed) == (1, 2, 0, 0)
    assert await _status(run, unit) == (
        UnitState.INGESTED,
        FetchStatus.UNKNOWN,
        None,
        2,
    )
    cursor = await run.conn.execute(
        "SELECT instrument_id, activation_ns, expiration_ns, raw_symbol, asset,"
        " exchange, min_price_increment, contract_multiplier, unit_id"
        " FROM tick_definition ORDER BY instrument_id"
    )
    spread, outright = await cursor.fetchall()
    assert outright == (
        42_035_063,
        ACTIVATION,
        EXPIRATION,
        "ESZ4",
        "ES",
        "XCME",
        250_000_000,
        50,
        unit,
    )
    assert spread[3] == "ESH6-ESU6"
    assert spread[4] is None and spread[6] is None  # undefined → NULL


async def test_an_identical_resend_is_a_noop_and_still_ingests(run: TickRun) -> None:
    first = await _unit(run, DAY1, [ESZ4, SPREAD])
    await project_definitions(run, READER)
    resend = [r | {"ts_recv": 999} for r in (ESZ4, SPREAD)]
    second = await _unit(run, DAY2, resend)
    tally = await project_definitions(run, READER)
    assert (tally.projected, tally.inserted, tally.noops) == (1, 0, 2)
    assert (await _status(run, second))[0] is UnitState.INGESTED
    assert await _count(run) == 2
    assert (await _status(run, first))[0] is UnitState.INGESTED


async def test_one_changed_kept_field_fails_the_unit_naming_it(run: TickRun) -> None:
    await _unit(run, DAY1, [ESZ4])
    await project_definitions(run, READER)
    changed = await _unit(run, DAY2, [ESZ4 | {"contract_multiplier": 25}])
    tally = await project_definitions(run, READER)
    assert tally.failed == 1
    state, status, reason, _ = await _status(run, changed)
    assert (state, status) == (UnitState.VERIFIED, FetchStatus.RETRY_EXHAUSTED)
    assert reason is not None
    assert "contract_multiplier" in reason and "42035063" in reason and "ESZ4" in reason
    assert await _count(run) == 1


@pytest.mark.parametrize("field", ["activation", "expiration"])
async def test_an_undefined_window_fails_the_unit_naming_the_instrument(
    run: TickRun, field: str
) -> None:
    unit = await _unit(run, DAY1, [ESZ4, SPREAD | {field: UNDEF_TS}])
    tally = await project_definitions(run, READER)
    assert tally.failed == 1
    _, status, reason, _ = await _status(run, unit)
    assert status is FetchStatus.RETRY_EXHAUSTED
    assert reason is not None and "42004904" in reason and "ESH6-ESU6" in reason
    assert await _count(run) == 0  # the good record was not written either


async def test_an_overlapping_window_for_a_reused_id_names_both_windows(
    run: TickRun,
) -> None:
    await _unit(run, DAY1, [ESZ4])
    await project_definitions(run, READER)
    later = ACTIVATION + 30 * NS_PER_DAY  # a different key, inside the first window
    unit = await _unit(
        run, DAY2, [ESZ4 | {"activation": later, "raw_symbol": b"ESZ4X"}]
    )
    tally = await project_definitions(run, READER)
    assert tally.failed == 1
    _, status, reason, _ = await _status(run, unit)
    assert status is FetchStatus.RETRY_EXHAUSTED
    assert reason is not None
    assert str(later) in reason and str(ACTIVATION) in reason
    assert await _count(run) == 1


async def test_a_reused_id_with_a_disjoint_window_is_accepted(run: TickRun) -> None:
    await _unit(run, DAY1, [ESZ4])
    after = EXPIRATION + NS_PER_DAY
    await _unit(
        run, DAY2, [ESZ4 | {"activation": after, "expiration": after + 90 * NS_PER_DAY}]
    )
    tally = await project_definitions(run, READER)
    assert (tally.projected, tally.inserted, tally.failed) == (2, 2, 0)


async def test_duplicates_inside_one_file(run: TickRun) -> None:
    same = await _unit(run, DAY1, [ESZ4, ESZ4 | {"ts_recv": 7}])
    differ = await _unit(run, DAY2, [SPREAD, SPREAD | {"currency": b"EUR"}])
    tally = await project_definitions(run, READER)
    assert (tally.projected, tally.inserted, tally.failed) == (1, 1, 1)
    assert (await _status(run, same))[0] is UnitState.INGESTED
    _, status, reason, _ = await _status(run, differ)
    assert status is FetchStatus.RETRY_EXHAUSTED
    assert reason is not None and "currency" in reason


async def test_the_phase_projects_only_definition_units(run: TickRun) -> None:
    trades = await _unit(run, DAY1, [ESZ4], schema=TickSchema.TRADES)
    definition = await _unit(run, DAY2, [ESZ4])
    tally = await project_definitions(run, READER)
    assert tally.projected == 1
    assert (await _status(run, trades))[0] is UnitState.VERIFIED
    assert (await _status(run, definition))[0] is UnitState.INGESTED


async def test_units_project_earliest_day_first(run: TickRun) -> None:
    later = await _unit(run, DAY3, [ESZ4 | {"ts_recv": 3}])
    earlier = await _unit(run, DAY1, [ESZ4 | {"ts_recv": 1}])
    await project_definitions(run, READER)
    cursor = await run.conn.execute("SELECT unit_id, ts_recv_ns FROM tick_definition")
    assert await cursor.fetchall() == [(earlier, 1)]  # the first day's row is kept
    assert (await _status(run, later))[0] is UnitState.INGESTED


async def test_a_failed_unit_is_not_retried_by_the_next_run(run: TickRun) -> None:
    unit = await _unit(run, DAY1, [ESZ4 | {"activation": UNDEF_TS}])
    await project_definitions(run, READER)
    again = await project_definitions(run, READER)
    assert again.projected == 0 and again.failed == 0
    assert (await units_of_request(run.conn, 1))[
        0
    ].fetch_status is FetchStatus.RETRY_EXHAUSTED
    assert unit
