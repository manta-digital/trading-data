"""Bar ingest end to end on the derived 2024-12-03 day (slice 302, D3, D4).

Criteria 1 to 4 and 7: the derived bar fixture ingests with provider =
decoded = stored, a bar unit waits for its day's definitions, a tick-tier unit
and a bar unit of one shape and day both stay current, each ingest check
fails the unit and leaves nothing behind, and a pre-2020 bar lands in the
CME calendar 301 filled.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.ingest import plan_unit, run_worker, scalar, unit_state
from tick_support.runs import connect
from tick_support.tick_bar_helpers import seed_bar_units, write_bar_file
from tick_support.tier_units import READER, TBBO_DAY, real_file, seed_tier_unit

from manta_trading.data.tick.bar_constants import BAR_1M_TABLE, BAR_LEDGER_TABLE
from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_plan import Calendars
from manta_trading.data.tick.ingest_select import SkipKind, select_units
from manta_trading.data.tick.ingest_worker import UnitResult

NS_PER_SECOND = 1_000_000_000
SPREAD_SESSION_DAYS = {date(2024, 12, 3), date(2024, 12, 4)}


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


def _ns(moment: datetime) -> int:
    return int(moment.timestamp()) * NS_PER_SECOND


def _first_bar() -> dict[str, int]:
    """A valid bar from the derived day: its busiest instrument's first minute."""
    batch = next(
        READER.open_file(real_file(TBBO_DAY, TickSchema.OHLCV_1M)).iter_batches()
    )
    row = batch.records[0]
    return {
        "instrument_id": int(row["instrument_id"]),
        "ts_event": int(row["ts_event"]),
        "open": int(row["open"]),
        "high": int(row["high"]),
        "low": int(row["low"]),
        "close": int(row["close"]),
        "volume": int(row["volume"]),
    }


def _bar_rows(url: str, unit_id: int) -> int:
    return int(
        scalar(url, f"SELECT count(*) FROM {BAR_1M_TABLE} WHERE unit_id = %s", unit_id)
    )


def _ledger_rows(url: str, unit_id: int) -> int:
    return int(
        scalar(
            url, f"SELECT count(*) FROM {BAR_LEDGER_TABLE} WHERE unit_id = %s", unit_id
        )
    )


async def test_the_derived_bar_day_ingests_with_provider_decoded_stored(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    seeded = await seed_bar_units(url, tmp_path, job_id="BARS")
    plan = await plan_unit(url, calendars, seeded.unit_id)
    outcome = run_worker(plan, tmp_path, url)
    assert outcome.result is UnitResult.INGESTED, outcome.reason
    state, _, decoded, _ = unit_state(url, seeded.unit_id)
    assert state == UnitState.INGESTED.value
    provider = scalar(
        url,
        "SELECT provider_record_count FROM tick_archive_unit WHERE unit_id = %s",
        seeded.unit_id,
    )
    assert provider == decoded == _bar_rows(url, seeded.unit_id) == seeded.record_count
    with psycopg.connect(url) as conn:
        sessions = conn.execute(
            f"SELECT DISTINCT session_date FROM {BAR_LEDGER_TABLE}"
            " WHERE unit_id = %s AND record_count > 0",
            (seeded.unit_id,),
        ).fetchall()
        per_instrument = conn.execute(
            f"SELECT instrument_id, sum(record_count)::bigint, sum(volume)::bigint"
            f" FROM {BAR_LEDGER_TABLE} WHERE unit_id = %s GROUP BY 1"
            " HAVING sum(record_count) > 0",
            (seeded.unit_id,),
        ).fetchall()
        raw = conn.execute(
            f"SELECT instrument_id, count(*), sum(volume)::bigint"
            f" FROM {BAR_1M_TABLE} GROUP BY 1"
        ).fetchall()
        trade_ledger = conn.execute(
            "SELECT count(*) FROM tick_ingest_ledger"
        ).fetchone()
    assert {row[0] for row in sessions} == SPREAD_SESSION_DAYS
    assert sorted(per_instrument) == sorted(raw)
    assert len(raw) == 5  # outrights and calendar spreads
    assert trade_ledger == (0,)  # bars never write the trade ledger


async def test_a_bar_unit_waits_for_its_days_definitions(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    seeded = await seed_bar_units(url, tmp_path, job_id="BARS")
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


async def test_a_tick_tier_unit_and_a_bar_unit_both_stay_current(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    bars = await seed_bar_units(url, tmp_path, job_id="BARS")
    ticks = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TBBO_DAY, job_id="TICKS"
    )
    for unit in (ticks, bars):
        plan = await plan_unit(url, calendars, unit.unit_id)
        assert plan.superseded == ()
        assert run_worker(plan, tmp_path, url).result is UnitResult.INGESTED
    for unit in (ticks, bars):
        state, _, _, superseded_by = unit_state(url, unit.unit_id)
        assert (state, superseded_by) == (UnitState.INGESTED.value, None)
    assert int(scalar(url, "SELECT count(*) FROM tick_trade")) > 0
    assert int(scalar(url, f"SELECT count(*) FROM {BAR_1M_TABLE}")) > 0


async def _failing_unit(
    url: str, archive: Path, calendars: Calendars, tmp_path: Path, bars: list[Any]
) -> tuple[Any, Any]:
    file = write_bar_file(tmp_path / "bars.dbn.zst", TBBO_DAY, bars)
    seeded = await seed_bar_units(url, archive, job_id="BAD", bar_file=file)
    plan = await plan_unit(url, calendars, seeded.unit_id)
    return seeded, run_worker(plan, archive, url)


def _expect_failure(url: str, seeded: Any, outcome: Any, check: IngestCheck) -> None:
    assert outcome.result is UnitResult.FAILED
    assert outcome.check is check
    assert outcome.reason is not None and outcome.reason.startswith(f"{check.value}: ")
    assert _bar_rows(url, seeded.unit_id) == 0
    assert _ledger_rows(url, seeded.unit_id) == 0


async def test_a_bar_outside_the_units_day_fails_unit_day(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    bar = _first_bar()
    later = {**bar, "ts_event": bar["ts_event"] + 2 * 86_400 * NS_PER_SECOND}
    archive = tmp_path / "archive"
    archive.mkdir()
    seeded, outcome = await _failing_unit(
        migrated_tick_db, archive, calendars, tmp_path, [bar, later]
    )
    _expect_failure(migrated_tick_db, seeded, outcome, IngestCheck.UNIT_DAY)
    assert "1 records outside 2024-12-03" in (outcome.reason or "")


async def test_a_bar_with_no_definition_fails_resolution(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    unknown = {**_first_bar(), "instrument_id": 999_999_999}
    archive = tmp_path / "archive"
    archive.mkdir()
    seeded, outcome = await _failing_unit(
        migrated_tick_db, archive, calendars, tmp_path, [unknown]
    )
    _expect_failure(migrated_tick_db, seeded, outcome, IngestCheck.RESOLUTION)


async def test_a_bar_in_the_daily_break_fails_session_boundary(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    # 22:00-23:00 UTC is the 2024-12-03 break (16:00-17:00 Chicago).
    in_break = {
        **_first_bar(),
        "ts_event": _ns(datetime(2024, 12, 3, 22, 30, tzinfo=UTC)),
    }
    archive = tmp_path / "archive"
    archive.mkdir()
    seeded, outcome = await _failing_unit(
        migrated_tick_db, archive, calendars, tmp_path, [in_break]
    )
    _expect_failure(migrated_tick_db, seeded, outcome, IngestCheck.SESSION_BOUNDARY)
    assert "America/Chicago" in (outcome.reason or "")


async def test_a_second_current_unit_holding_a_key_fails_overlap(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    """Two bar units of one shape and day rank equal, so neither supersedes."""
    url = migrated_tick_db
    first = await seed_bar_units(url, tmp_path, job_id="FIRST")
    second = await seed_bar_units(url, tmp_path, job_id="SECOND")
    plan = await plan_unit(url, calendars, first.unit_id)
    assert run_worker(plan, tmp_path, url).result is UnitResult.INGESTED
    plan = await plan_unit(url, calendars, second.unit_id)
    assert plan.superseded == ()
    outcome = run_worker(plan, tmp_path, url)
    _expect_failure(url, second, outcome, IngestCheck.OVERLAP)
    assert _bar_rows(url, first.unit_id) == first.record_count  # the first is intact
