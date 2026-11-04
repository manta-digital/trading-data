"""Statistics ingest end to end on the real 2024-12-03 definitions (slice 303, D3, D4).

A statistics file is built with the project's file-writing helpers (records in
the reader's own layout) under a header spanning the unit's day. Its
definitions are the committed real 2024-12-03 slice. Each ingest check fails
the unit and leaves nothing behind; provider = decoded = stored.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.ingest import plan_unit, run_worker, scalar, unit_state
from tick_support.runs import connect
from tick_support.statistics_records import (
    ns_of,
    referenced,
    seed_statistics_units,
    stat,
)
from tick_support.tier_units import READER, TBBO_DAY, real_file

from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_plan import Calendars
from manta_trading.data.tick.ingest_select import SkipKind, select_units
from manta_trading.data.tick.ingest_worker import UnitResult
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_LEDGER_TABLE,
    STATISTICS_TABLE,
    StatType,
)
from manta_trading.data.tick.statistics_projector import (
    COPY_TYPES,
    STATISTICS_PROJECTOR,
)
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START

#: The unit's day is the committed definitions' day. Its statistics refer to the
#: previous session (Monday) and to the unit's own date.
DAY = TBBO_DAY
PREVIOUS_SESSION = date(2024, 12, 2)
BEFORE_DATASET = date(CME_DATASET_START.year, 1, 4)


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


def _first_trade() -> tuple[int, int]:
    """A real instrument of the day's definitions and a time inside the day."""
    batch = next(READER.open_file(real_file(DAY, TickSchema.TRADES)).iter_batches())
    return int(batch.records["instrument_id"][0]), int(batch.records["ts_event"][0])


def _valid_rows() -> list[dict[str, Any]]:
    instrument, recv = _first_trade()
    return [
        referenced(
            instrument, StatType.SETTLEMENT_PRICE, PREVIOUS_SESSION, ts_recv=recv
        ),
        referenced(
            instrument,
            StatType.SETTLEMENT_PRICE,
            PREVIOUS_SESSION,
            ts_recv=recv + 7,
            sequence=2,
            price=200_000_000_000,
        ),  # fmt: skip
        referenced(
            instrument,
            StatType.CLEARED_VOLUME,
            PREVIOUS_SESSION,
            ts_recv=recv + 1,
            sequence=3,
        ),  # fmt: skip
        referenced(
            instrument, StatType.OPEN_INTEREST, DAY, ts_recv=recv + 2, sequence=4
        ),
        stat(instrument, 7, ts_recv=recv + 3, sequence=5),  # LOWEST_OFFER: sessionless
    ]


def _count(url: str, table: str, unit_id: int) -> int:
    return int(scalar(url, f"SELECT count(*) FROM {table} WHERE unit_id = %s", unit_id))


async def test_a_statistics_file_ingests_with_provider_decoded_stored(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    rows = _valid_rows()
    seeded = await seed_statistics_units(url, tmp_path / "a", DAY, rows, job_id="STATS")
    plan = await plan_unit(url, calendars, seeded.unit_id)
    assert plan.reference_calendar is not None
    outcome = run_worker(plan, tmp_path / "a", url)
    assert outcome.result is UnitResult.INGESTED, outcome.reason
    assert outcome.notes == ()
    state, _, decoded, _ = unit_state(url, seeded.unit_id)
    assert state == UnitState.INGESTED.value
    provider = scalar(
        url,
        "SELECT provider_record_count FROM tick_archive_unit WHERE unit_id = %s",
        seeded.unit_id,
    )
    stored = _count(url, STATISTICS_TABLE, seeded.unit_id)
    assert provider == decoded == stored == seeded.record_count == len(rows)
    with psycopg.connect(url) as conn:
        ledger = conn.execute(
            "SELECT stat_type, session_date, record_count"
            f" FROM {STATISTICS_LEDGER_TABLE}"
            " WHERE unit_id = %s ORDER BY stat_type, session_date",
            (seeded.unit_id,),
        ).fetchall()
        sessionless = conn.execute(
            f"SELECT count(*) FROM {STATISTICS_TABLE}"
            " WHERE unit_id = %s AND session_date IS NULL AND calendar_id IS NULL",
            (seeded.unit_id,),
        ).fetchone()
        trade_ledger = conn.execute(
            "SELECT count(*) FROM tick_ingest_ledger"
        ).fetchone()
    assert ledger == [
        (StatType.SETTLEMENT_PRICE, PREVIOUS_SESSION, 2),
        (StatType.CLEARED_VOLUME, PREVIOUS_SESSION, 1),
        (StatType.OPEN_INTEREST, DAY, 1),
    ]
    assert sessionless == (1,)  # the LOWEST_OFFER record
    assert trade_ledger == (0,)  # statistics never write the trade ledger


async def test_a_statistics_unit_waits_for_its_days_definitions(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    seeded = await seed_statistics_units(
        url, tmp_path / "a", DAY, _valid_rows(), job_id="STATS"
    )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_archive_unit SET state = %s WHERE unit_id = %s",
            (UnitState.VERIFIED.value, seeded.definition_unit_id),
        )
    async with connect(url) as aconn:
        waiting = await select_units(aconn)
    assert [s.kind for s in waiting.skipped] == [SkipKind.AWAITING_DEFINITIONS]
    assert waiting.selected == ()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_archive_unit SET state = %s WHERE unit_id = %s",
            (UnitState.INGESTED.value, seeded.definition_unit_id),
        )
    async with connect(url) as aconn:
        ready = await select_units(aconn)
    assert [u.unit_id for u in ready.selected] == [seeded.unit_id]


async def _failing_unit(
    url: str, tmp_path: Path, calendars: Calendars, rows: list[dict[str, Any]]
) -> tuple[Any, Any]:
    archive = tmp_path / "archive"
    archive.mkdir()
    seeded = await seed_statistics_units(url, archive, DAY, rows, job_id="BAD")
    plan = await plan_unit(url, calendars, seeded.unit_id)
    return seeded, run_worker(plan, archive, url)


def _expect_failure(url: str, seeded: Any, outcome: Any, check: IngestCheck) -> None:
    assert outcome.result is UnitResult.FAILED
    assert outcome.check is check
    assert outcome.reason is not None and outcome.reason.startswith(f"{check.value}: ")
    assert _count(url, STATISTICS_TABLE, seeded.unit_id) == 0
    assert _count(url, STATISTICS_LEDGER_TABLE, seeded.unit_id) == 0


async def test_a_reference_on_a_weekend_fails_stat_reference(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    instrument, recv = _first_trade()
    rows = [
        referenced(instrument, StatType.CLEARED_VOLUME, date(2024, 12, 1), ts_recv=recv)
    ]
    seeded, outcome = await _failing_unit(migrated_tick_db, tmp_path, calendars, rows)
    _expect_failure(migrated_tick_db, seeded, outcome, IngestCheck.STAT_REFERENCE)
    assert "not a session" in outcome.reason


async def test_a_record_received_outside_the_units_day_fails_unit_day(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    instrument, recv = _first_trade()
    later = ns_of(date(2024, 12, 5), 1)
    rows = [
        referenced(instrument, StatType.CLEARED_VOLUME, PREVIOUS_SESSION, ts_recv=recv),
        referenced(
            instrument,
            StatType.CLEARED_VOLUME,
            PREVIOUS_SESSION,
            ts_recv=later,
            ts_event=recv,
            sequence=2,
        ),  # fmt: skip
    ]
    seeded, outcome = await _failing_unit(migrated_tick_db, tmp_path, calendars, rows)
    _expect_failure(migrated_tick_db, seeded, outcome, IngestCheck.UNIT_DAY)
    assert "1 records outside 2024-12-03" in outcome.reason


async def test_an_instrument_with_no_definition_fails_resolution(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    _, recv = _first_trade()
    rows = [
        referenced(999_999_999, StatType.CLEARED_VOLUME, PREVIOUS_SESSION, ts_recv=recv)
    ]
    seeded, outcome = await _failing_unit(migrated_tick_db, tmp_path, calendars, rows)
    _expect_failure(migrated_tick_db, seeded, outcome, IngestCheck.RESOLUTION)


async def test_a_second_current_unit_holding_a_key_fails_overlap(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    """Two statistics units of one shape and day rank equal: neither supersedes."""
    url = migrated_tick_db
    rows = _valid_rows()
    first = await seed_statistics_units(url, tmp_path / "a", DAY, rows, job_id="FIRST")
    second = await seed_statistics_units(
        url, tmp_path / "b", DAY, rows, job_id="SECOND"
    )
    plan = await plan_unit(url, calendars, first.unit_id)
    assert run_worker(plan, tmp_path / "a", url).result is UnitResult.INGESTED
    plan = await plan_unit(url, calendars, second.unit_id)
    assert plan.superseded == ()
    outcome = run_worker(plan, tmp_path / "b", url)
    _expect_failure(url, second, outcome, IngestCheck.OVERLAP)
    assert _count(url, STATISTICS_TABLE, first.unit_id) == len(rows)  # first intact


async def test_a_reference_before_the_dataset_is_stored_sessionless_and_counted(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    instrument, recv = _first_trade()
    rows = [
        referenced(instrument, StatType.SETTLEMENT_PRICE, BEFORE_DATASET, ts_recv=recv)
    ]
    seeded = await seed_statistics_units(url, tmp_path / "a", DAY, rows, job_id="OLD")
    plan = await plan_unit(url, calendars, seeded.unit_id)
    outcome = run_worker(plan, tmp_path / "a", url)
    assert outcome.result is UnitResult.INGESTED, outcome.reason
    assert outcome.notes == (f"1 records reference dates before {CME_DATASET_START}",)
    with psycopg.connect(url) as conn:
        stored = conn.execute(
            f"SELECT session_date, calendar_id, ts_ref FROM {STATISTICS_TABLE}"
            " WHERE unit_id = %s",
            (seeded.unit_id,),
        ).fetchall()
    assert stored == [(None, None, ns_of(BEFORE_DATASET, 16))]
    assert _count(url, STATISTICS_LEDGER_TABLE, seeded.unit_id) == 0


async def test_reingesting_after_delete_unit_is_idempotent(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    rows = _valid_rows()
    first = await seed_statistics_units(url, tmp_path / "a", DAY, rows, job_id="FIRST")
    second = await seed_statistics_units(
        url, tmp_path / "b", DAY, rows, job_id="SECOND"
    )
    plan = await plan_unit(url, calendars, first.unit_id)
    assert run_worker(plan, tmp_path / "a", url).result is UnitResult.INGESTED
    first_ns, last_ns = (
        int(scalar(url, f"SELECT {agg}(ts_recv) FROM {STATISTICS_TABLE}"))
        for agg in ("min", "max")
    )
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        for _ in range(2):  # deleting twice is the same as once
            STATISTICS_PROJECTOR.delete_unit(cur, first.unit_id, first_ns, last_ns)
        conn.commit()
    assert _count(url, STATISTICS_TABLE, first.unit_id) == 0
    plan = await plan_unit(url, calendars, second.unit_id)
    outcome = run_worker(plan, tmp_path / "b", url)
    assert outcome.result is UnitResult.INGESTED, outcome.reason
    assert _count(url, STATISTICS_TABLE, second.unit_id) == len(rows)


def test_the_copy_types_follow_the_table_ddl(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        rows = conn.execute(
            "SELECT data_type FROM information_schema.columns"
            f" WHERE table_name = '{STATISTICS_TABLE}' ORDER BY ordinal_position"
        ).fetchall()
    names = {
        "bigint": "int8",
        "integer": "int4",
        "smallint": "int2",
        "text": "text",
        "date": "date",
    }
    assert tuple(names[row[0]] for row in rows) == COPY_TYPES


def test_the_reference_calendar_holds_session_dates_and_is_cached(
    calendars: Calendars,
) -> None:
    reference = calendars.reference_calendar("ES", DAY)
    assert reference.calendar_id == "CME_EQUITY"
    assert PREVIOUS_SESSION in reference.session_dates
    assert date(2024, 12, 1) not in reference.session_dates  # a Sunday
    assert calendars.reference_calendar("ES", PREVIOUS_SESSION) is reference
    assert calendars.reference_calendar("ES", date(2024, 12, 4)) is not reference
