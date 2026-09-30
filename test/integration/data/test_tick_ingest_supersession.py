"""Tier supersession and overlap in the worker (slice 225, TD5; FR5, FR6).

The ``trades`` file for 2024-12-03 is derived from the ``tbbo`` slice's trade
fields (the adopted jobs share no day; see ``SOURCES.md``), so the two tiers
hold the same events.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from tick_support.ingest import (
    ledger_count,
    plan_unit,
    run_worker,
    scalar,
    trade_count,
    unit_state,
)
from tick_support.tier_units import TBBO_DAY, SeededUnit, seed_tier_unit

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_plan import Calendars
from manta_trading.data.tick.ingest_worker import UnitResult

#: Current units' session totals: superseded units drop out through the join.
CURRENT_TOTALS = (
    "SELECT l.session_date, sum(l.record_count), sum(l.volume)"
    " FROM tick_ingest_ledger l JOIN tick_archive_unit u USING (unit_id)"
    " WHERE u.superseded_by_unit_id IS NULL GROUP BY 1 ORDER BY 1"
)
UNIT_TOTALS = (
    "SELECT session_date, sum(record_count), sum(volume) FROM tick_ingest_ledger"
    " WHERE unit_id = %s GROUP BY 1 ORDER BY 1"
)


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


async def _loaded_trades(
    url: str, archive: Path, calendars: Calendars, job_id: str = "TRADES"
) -> SeededUnit:
    trades = await seed_tier_unit(
        url, archive, TickSchema.TRADES, TBBO_DAY, job_id=job_id
    )
    plan = await plan_unit(url, calendars, trades.unit_id)
    assert run_worker(plan, archive, url).result is UnitResult.INGESTED
    return trades


def _all(url: str, query: str, *params: object) -> list[tuple[object, ...]]:
    with psycopg.connect(url) as conn:
        return conn.execute(query, params).fetchall()  # type: ignore[arg-type]


async def test_tbbo_replaces_an_ingested_trades_unit_in_one_transaction(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    trades = await _loaded_trades(url, tmp_path, calendars)
    trades_ledger = ledger_count(url, trades.unit_id)
    tbbo = await seed_tier_unit(url, tmp_path, TickSchema.TBBO, TBBO_DAY, job_id="TBBO")
    plan = await plan_unit(url, calendars, tbbo.unit_id)
    assert [old.unit_id for old in plan.superseded] == [trades.unit_id]
    assert run_worker(plan, tmp_path, url).result is UnitResult.INGESTED
    assert unit_state(url, trades.unit_id)[3] == tbbo.unit_id
    assert trade_count(url, trades.unit_id) == 0
    assert ledger_count(url, trades.unit_id) == trades_ledger  # kept as history
    assert trade_count(url, tbbo.unit_id) == tbbo.record_count
    assert _all(url, CURRENT_TOTALS) == _all(url, UNIT_TOTALS, tbbo.unit_id)
    bbo_nulls = scalar(url, "SELECT count(*) FROM tick_trade WHERE bid_px_00 IS NULL")
    assert bbo_nulls == 0


async def test_a_failure_after_supersession_leaves_the_trades_unit_untouched(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    trades = await _loaded_trades(url, tmp_path, calendars)
    tbbo = await seed_tier_unit(
        url,
        tmp_path,
        TickSchema.TBBO,
        TBBO_DAY,
        job_id="TBBO",
        provider_record_count=1,
    )
    plan = await plan_unit(url, calendars, tbbo.unit_id)
    outcome = run_worker(plan, tmp_path, url)
    assert (outcome.result, outcome.check) == (UnitResult.FAILED, IngestCheck.COUNTS)
    assert unit_state(url, trades.unit_id) == (
        "ingested",
        "UNKNOWN",
        trades.record_count,
        None,
    )
    assert trade_count(url, trades.unit_id) == trades.record_count
    assert trade_count(url, tbbo.unit_id) == 0


async def test_a_second_current_unit_colliding_with_loaded_rows_fails_as_overlap(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    first = await _loaded_trades(url, tmp_path, calendars)
    second = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TBBO_DAY, job_id="AGAIN"
    )
    plan = await plan_unit(url, calendars, second.unit_id)
    assert plan.superseded == ()  # same tier: nothing to replace
    outcome = run_worker(plan, tmp_path, url)
    assert (outcome.result, outcome.check) == (UnitResult.FAILED, IngestCheck.OVERLAP)
    reason = outcome.reason or ""
    assert reason.startswith(
        "overlap: Key (instrument_id, ts_event, sequence, sequence_ordinal)=("
    )
    assert ") already exists. other current units" in reason
    assert f"other current units on {TBBO_DAY}: [{first.unit_id}]" in reason
    assert trade_count(url, second.unit_id) == 0
    assert trade_count(url, first.unit_id) == first.record_count
