"""Per-unit ingest plans on a real day (slice 225, TD5–TD7).

The tick database is ``migrated_tick_db`` with a real slice seeded; the
calendar is ``session_migrated_db`` (CME_EQUITY populated by migration 058).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg_pool import PoolTimeout
from tick_support.rows import (
    FILE_COLUMNS,
    insert_ledger_row,
    insert_request,
    insert_unit,
)
from tick_support.runs import connect
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit

from manta_trading.data.base.trading_calendar import TradingCalendar
from manta_trading.data.tick.constants import SType, TickSchema, UnitState
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_plan import Calendars, build_plan
from manta_trading.data.tick.ingest_records import UnitCheckFailed
from manta_trading.data.tick.manifest_reads import units_by_id
from manta_trading.data.tick.tick_calendar import TickCalendarError

AConn = psycopg.AsyncConnection[Any]


@pytest.fixture
async def aconn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with connect(migrated_tick_db) as conn:
        yield conn


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


async def _plan(aconn: AConn, calendars: Calendars, unit_id: int) -> Any:
    (unit,) = await units_by_id(aconn, [unit_id])
    return await build_plan(aconn, calendars, unit)


def _unit(url: str, day: date, **request: Any) -> int:
    with psycopg.connect(url, autocommit=True) as conn:
        request_id = insert_request(conn, range_start=day, **request)
        return insert_unit(
            conn,
            request_id,
            unit_date=day,
            state=UnitState.VERIFIED.value,
            **FILE_COLUMNS,
        )


async def test_a_real_day_plans_two_sessions_and_its_definitions(
    migrated_tick_db: str, tmp_path: Path, aconn: AConn, calendars: Calendars
) -> None:
    seeded = await seed_tier_unit(
        migrated_tick_db, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="J"
    )
    plan = await _plan(aconn, calendars, seeded.unit_id)
    sessions = plan.frame.index.sessions
    assert [s.session_date for s in sessions] == [date(2024, 9, 3), date(2024, 9, 4)]
    assert (plan.product, plan.calendar_id) == ("ES", "CME_EQUITY")
    assert str(plan.frame.zone) == "America/Chicago"
    # The day's file has 61 records: 21 outrights and 20 spreads, each spread
    # sent twice under one key, so 41 instruments (not the LLD's "61").
    assert len(set(plan.definitions.instrument_id.tolist())) == 41
    assert plan.superseded == ()


async def test_a_raw_symbol_unit_fails_as_shape(
    migrated_tick_db: str, aconn: AConn, calendars: Calendars
) -> None:
    unit = _unit(
        migrated_tick_db,
        TRADES_DAY,
        stype_in=SType.RAW_SYMBOL.value,
        symbols=["ESZ4"],
    )
    with pytest.raises(UnitCheckFailed) as info:
        await _plan(aconn, calendars, unit)
    assert info.value.check is IngestCheck.SHAPE
    assert info.value.reason.startswith("shape: stype_in raw_symbol")


async def test_a_day_past_the_populated_span_fails_as_session_boundary(
    migrated_tick_db: str, aconn: AConn, calendars: Calendars
) -> None:
    unit = _unit(migrated_tick_db, date(2031, 1, 6), range_end=date(2031, 1, 7))
    with pytest.raises(UnitCheckFailed) as info:
        await _plan(aconn, calendars, unit)
    assert info.value.check is IngestCheck.SESSION_BOUNDARY
    assert "day 2031-01-06 is outside the populated calendar range" in (
        info.value.reason
    )
    assert "mt data extend" in info.value.reason


async def test_an_unreachable_calendar_raises_tick_calendar_error(
    migrated_tick_db: str,
    aconn: AConn,
    calendars: Calendars,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(*args: Any) -> list[Any]:
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    monkeypatch.setattr(TradingCalendar, "sessions_between", unreachable)
    unit = _unit(migrated_tick_db, TRADES_DAY)
    with pytest.raises(TickCalendarError, match="CME_EQUITY unavailable"):
        await _plan(aconn, calendars, unit)


async def test_a_tbbo_plan_supersedes_the_loaded_trades_unit_with_ledger_bounds(
    migrated_tick_db: str, tmp_path: Path, aconn: AConn, calendars: Calendars
) -> None:
    trades = await seed_tier_unit(
        migrated_tick_db, tmp_path, TickSchema.TRADES, TBBO_DAY, job_id="T"
    )
    tbbo = await seed_tier_unit(
        migrated_tick_db, tmp_path, TickSchema.TBBO, TBBO_DAY, job_id="B"
    )
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_archive_unit SET state = 'ingested' WHERE unit_id = %s",
            (trades.unit_id,),
        )
        insert_ledger_row(
            conn,
            trades.unit_id,
            session_date=date(2024, 12, 3),
            first_event_ns=100,
            last_event_ns=200,
        )
        insert_ledger_row(
            conn,
            trades.unit_id,
            instrument_id=5002,
            session_date=date(2024, 12, 3),
            first_event_ns=50,
            last_event_ns=300,
        )
        insert_ledger_row(
            conn,
            trades.unit_id,
            instrument_id=13388,
            session_date=date(2024, 12, 4),
            record_count=0,
            volume=0,
            first_event_ns=None,
            last_event_ns=None,
        )
    plan = await _plan(aconn, calendars, tbbo.unit_id)
    assert [(s.unit_id, s.state) for s in plan.superseded] == [
        (trades.unit_id, UnitState.INGESTED)
    ]
    assert (plan.superseded[0].first_event_ns, plan.superseded[0].last_event_ns) == (
        50,
        300,
    )
    trades_plan = await _plan(aconn, calendars, trades.unit_id)
    assert trades_plan.superseded == ()
