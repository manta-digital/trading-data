"""Supersession is scoped to a schema family (slice 300, D1; criterion 2).

A tick tier outranks a lower tier of the same shape and day (today's rule); a
definition unit and a tier unit of the same shape and day never outrank each
other, in either direction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit
from tick_support.runs import connect
from tick_support.tier_units import TRADES_DAY

from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.ingest_plan import Calendars, build_plan
from manta_trading.data.tick.ingest_select import SkipKind, select_units
from manta_trading.data.tick.manifest_reads import units_by_id

DAY = date(2024, 9, 3)
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


def _unit(url: str, schema: TickSchema, day: date, state: UnitState) -> int:
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(
            conn,
            schema=schema.value,
            range_start=day,
            range_end=day + timedelta(days=1),
        )
        return insert_unit(
            conn, request, unit_date=day, state=state.value, **FILE_COLUMNS
        )


async def _superseded(aconn: AConn, calendars: Calendars, unit_id: int) -> Any:
    (unit,) = await units_by_id(aconn, [unit_id])
    return (await build_plan(aconn, calendars, unit)).superseded


async def test_a_definition_unit_never_outranks_a_tier_unit_in_select(
    migrated_tick_db: str, aconn: AConn
) -> None:
    trades = _unit(migrated_tick_db, TickSchema.TRADES, DAY, UnitState.VERIFIED)
    _unit(migrated_tick_db, TickSchema.DEFINITION, DAY, UnitState.INGESTED)
    selection = await select_units(aconn)
    assert [unit.unit_id for unit in selection.selected] == [trades]
    assert selection.skipped == ()


async def test_a_tier_unit_never_outranks_a_definition_unit_in_select(
    migrated_tick_db: str, aconn: AConn
) -> None:
    _unit(migrated_tick_db, TickSchema.DEFINITION, DAY, UnitState.INGESTED)
    definition = _unit(migrated_tick_db, TickSchema.DEFINITION, DAY, UnitState.VERIFIED)
    _unit(migrated_tick_db, TickSchema.TBBO, DAY, UnitState.VERIFIED)
    skipped = {
        s.unit_id: s.kind for s in (await select_units(aconn, [definition])).skipped
    }
    assert skipped == {definition: SkipKind.NOT_SELECTABLE}


async def test_a_definition_unit_is_not_superseded_by_a_tier_plan(
    migrated_tick_db: str, aconn: AConn, calendars: Calendars
) -> None:
    _unit(migrated_tick_db, TickSchema.DEFINITION, TRADES_DAY, UnitState.INGESTED)
    tbbo = _unit(migrated_tick_db, TickSchema.TBBO, TRADES_DAY, UnitState.VERIFIED)
    assert await _superseded(aconn, calendars, tbbo) == ()


async def test_a_tier_unit_is_not_superseded_by_a_definition_plan(
    migrated_tick_db: str, aconn: AConn, calendars: Calendars
) -> None:
    _unit(migrated_tick_db, TickSchema.TBBO, TRADES_DAY, UnitState.INGESTED)
    definition = _unit(
        migrated_tick_db, TickSchema.DEFINITION, TRADES_DAY, UnitState.VERIFIED
    )
    assert await _superseded(aconn, calendars, definition) == ()
