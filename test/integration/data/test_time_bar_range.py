"""Slice 309: a time-bar request's default range spans held ticks and bars."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from tick_support.roll_seed import seed_ledger, seed_tier_days
from tick_support.rows import (
    FILE_COLUMNS,
    insert_bar_ledger_row,
    insert_request,
    insert_unit,
)
from tick_support.runs import connect

from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.get_plan import TIME_BAR_RANGE, resolve_range
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.universe import TICK_UNIVERSE

AConn = psycopg.AsyncConnection[Any]
ES = next(e for e in TICK_UNIVERSE if e.product == "ES")
TICKS_DAY, BARS_DAY = date(2024, 9, 3), date(2024, 9, 10)
DAY = timedelta(days=1)


@pytest.fixture
async def aconn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with connect(migrated_tick_db) as conn:
        yield conn


def _seed_ticks(url: str) -> None:
    seed_ledger(url, seed_tier_days(url, [TICKS_DAY]), {(TICKS_DAY, 1): 1})


def _seed_bars(url: str) -> None:
    """A bar ledger row for a session with no ticks."""
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.OHLCV_1M.value)
        unit = insert_unit(
            conn,
            request,
            unit_date=BARS_DAY,
            state=UnitState.INGESTED.value,
            **FILE_COLUMNS,
        )
        insert_bar_ledger_row(conn, unit, session_date=BARS_DAY)


async def _default(conn: AConn) -> tuple[date, date] | None:
    return await resolve_range(
        conn, ES, None, None, SchemaFamily.TICK_TIER, TIME_BAR_RANGE
    )


async def test_nothing_held_is_no_range(aconn: AConn) -> None:
    assert await _default(aconn) is None


async def test_ticks_only(migrated_tick_db: str, aconn: AConn) -> None:
    _seed_ticks(migrated_tick_db)
    assert await _default(aconn) == (TICKS_DAY, TICKS_DAY + DAY)


async def test_bars_only_is_planned(migrated_tick_db: str, aconn: AConn) -> None:
    _seed_bars(migrated_tick_db)
    assert await _default(aconn) == (BARS_DAY, BARS_DAY + DAY)
    tick_only = await resolve_range(aconn, ES, None, None, SchemaFamily.TICK_TIER)
    assert tick_only is None  # raw ticks and tick bars keep the tick default


async def test_both_span_both_sessions(migrated_tick_db: str, aconn: AConn) -> None:
    _seed_ticks(migrated_tick_db)
    _seed_bars(migrated_tick_db)
    assert await _default(aconn) == (TICKS_DAY, BARS_DAY + DAY)
