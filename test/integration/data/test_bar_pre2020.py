"""Pre-2020 bars place into the CME calendar 301 filled (slice 302, D10).

Criterion 7 (placement) and criterion 11 (a pre-2020 definition with an
undefined window fails its unit, naming the instrument, and its bar unit
waits). Definitions and bars are hand-built for 2012-03-06, a Tuesday.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest
from tick_support.dbn_files import (
    CME_DEFINITION_RECORDS,
    definition_file_bytes,
)
from tick_support.ingest import plan_unit, run_worker, scalar, unit_state
from tick_support.runs import connect
from tick_support.tick_bar_helpers import seed_bar_units, write_bar_file

from manta_trading.data.tick.bar_constants import BAR_1M_TABLE, BAR_LEDGER_TABLE
from manta_trading.data.tick.constants import (
    CME_DATASET,
    UNDEF_TIMESTAMP_NS,
    SType,
    UnitState,
)
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_plan import Calendars
from manta_trading.data.tick.ingest_records import UnitCheckFailed
from manta_trading.data.tick.ingest_select import SkipKind, select_units
from manta_trading.data.tick.ingest_worker import UnitResult

DAY = date(2012, 3, 6)
INSTRUMENT = 8_800_001
UNDEFINED_INSTRUMENT = 8_800_002


def _ns(moment: datetime) -> int:
    return int(moment.timestamp()) * 1_000_000_000


# A 2012 ES contract: active from the previous December to mid-March.
ACTIVATION = _ns(datetime(2011, 12, 1, tzinfo=UTC))
EXPIRATION = _ns(datetime(2012, 3, 16, tzinfo=UTC))
BAR_TS = _ns(datetime(2012, 3, 6, 15, 0, tzinfo=UTC))  # 09:00 Chicago, mid-session


def _definition(instrument: int, symbol: bytes, **fields: int) -> dict[str, object]:
    return {
        **CME_DEFINITION_RECORDS[0],
        "instrument_id": instrument,
        "raw_symbol": symbol,
        "activation": ACTIVATION,
        "expiration": EXPIRATION,
        **fields,
    }


def _files(tmp_path: Path, definitions: list[dict[str, object]]) -> tuple[Path, Path]:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    definition = scratch / "glbx-mdp3-20120306.definition.dbn.zst"
    definition.write_bytes(
        definition_file_bytes(CME_DATASET, DAY, SType.PARENT, definitions)
    )
    bars = write_bar_file(
        scratch / "glbx-mdp3-20120306.ohlcv-1m.dbn.zst",
        DAY,
        [
            {
                "instrument_id": INSTRUMENT,
                "ts_event": BAR_TS,
                "open": 1_300_000_000_000,
                "high": 1_301_000_000_000,
                "low": 1_299_000_000_000,
                "close": 1_300_500_000_000,
                "volume": 12,
            }
        ],
    )
    return bars, definition


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


async def test_a_2012_bar_ingests_into_its_cme_equity_session(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    archive = tmp_path / "archive"
    archive.mkdir()
    bars, definition = _files(tmp_path, [_definition(INSTRUMENT, b"ESH2")])
    seeded = await seed_bar_units(
        url,
        archive,
        day=DAY,
        job_id="PRE2020",
        bar_file=bars,
        definition_file=definition,
    )
    plan = await plan_unit(url, calendars, seeded.unit_id)
    assert plan.calendar_id == "CME_EQUITY"
    outcome = run_worker(plan, archive, url)
    assert outcome.result is UnitResult.INGESTED, outcome.reason
    assert scalar(url, f"SELECT count(*) FROM {BAR_1M_TABLE}") == 1
    placed = scalar(
        url,
        f"SELECT session_date FROM {BAR_LEDGER_TABLE}"
        " WHERE record_count > 0 AND unit_id = %s",
        seeded.unit_id,
    )
    assert placed == DAY  # the Tuesday session that opened Monday evening


async def test_without_301s_pre_2020_sessions_the_same_bar_fails_session_boundary(
    migrated_tick_db: str, migrated_db: str, tmp_path: Path
) -> None:
    """A second throwaway primary database, with its pre-2020 sessions deleted.

    The fixture created the database, so the delete is permitted. It stands
    for the calendar as it was before slice 301.
    """
    url = migrated_tick_db
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM trading_sessions"
            " WHERE calendar_id = 'CME_EQUITY' AND session_date < '2020-01-01'"
        )
    archive = tmp_path / "archive"
    archive.mkdir()
    bars, definition = _files(tmp_path, [_definition(INSTRUMENT, b"ESH2")])
    seeded = await seed_bar_units(
        url, archive, day=DAY, job_id="NO301", bar_file=bars, definition_file=definition
    )
    old_calendars = Calendars(migrated_db)
    try:
        with pytest.raises(UnitCheckFailed) as failed:
            await plan_unit(url, old_calendars, seeded.unit_id)
    finally:
        old_calendars.close()
    assert failed.value.check is IngestCheck.SESSION_BOUNDARY
    assert "2012-03-06" in failed.value.reason
    assert "populated" in failed.value.reason


async def test_a_definition_with_an_undefined_window_fails_and_its_bars_wait(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    archive = tmp_path / "archive"
    archive.mkdir()
    bars, definition = _files(
        tmp_path,
        [
            _definition(INSTRUMENT, b"ESH2"),
            _definition(UNDEFINED_INSTRUMENT, b"ESM2", activation=UNDEF_TIMESTAMP_NS),
        ],
    )
    seeded = await seed_bar_units(
        url,
        archive,
        day=DAY,
        job_id="UNDEF",
        bar_file=bars,
        definition_file=definition,
        require_definitions=False,
    )
    state = unit_state(url, seeded.definition_unit_id)[0]
    assert state == UnitState.VERIFIED.value  # not ingested: nothing was written
    reason = scalar(
        url,
        "SELECT failure_reason FROM tick_archive_unit WHERE unit_id = %s",
        seeded.definition_unit_id,
    )
    assert str(UNDEFINED_INSTRUMENT) in reason and "no validity window" in reason
    assert scalar(url, "SELECT count(*) FROM tick_definition") == 0
    async with connect(url) as aconn:
        selection = await select_units(aconn)
    assert [(s.unit_id, s.kind) for s in selection.skipped] == [
        (seeded.unit_id, SkipKind.AWAITING_DEFINITIONS)
    ]
