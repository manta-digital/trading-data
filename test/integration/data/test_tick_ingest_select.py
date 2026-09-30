"""Which units ingest selects, and why the rest wait (slice 225, TD4; FR4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit
from tick_support.runs import connect

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    UNIT_STATES_WITH_FILE,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.ingest_select import SkipKind, select_units
from manta_trading.data.tick.manifest_repo import PROVIDER_HOLE_REASON

DAY1, DAY2 = date(2024, 9, 3), date(2024, 9, 4)
AT = datetime(2026, 9, 30, tzinfo=UTC)
AConn = psycopg.AsyncConnection[Any]


@pytest.fixture
def db(migrated_tick_db: str) -> Any:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def aconn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with connect(migrated_tick_db) as conn:
        yield conn


def _unit(
    db: Any,
    schema: TickSchema,
    day: date,
    state: UnitState = UnitState.VERIFIED,
    **unit: Any,
) -> int:
    request = insert_request(
        db, schema=schema.value, range_start=day, range_end=day + timedelta(days=1)
    )
    files = FILE_COLUMNS if state in UNIT_STATES_WITH_FILE else {}
    return insert_unit(db, request, unit_date=day, state=state.value, **files | unit)


def _definitions(db: Any, day: date, state: UnitState = UnitState.INGESTED) -> int:
    return _unit(db, TickSchema.DEFINITION, day, state)


def _ids(selection: Any) -> list[int]:
    return [unit.unit_id for unit in selection.selected]


def _skips(selection: Any) -> dict[int, tuple[SkipKind, str]]:
    return {s.unit_id: (s.kind, s.detail) for s in selection.skipped}


async def test_a_unit_awaits_its_definitions_then_is_selected(
    db: Any, aconn: AConn
) -> None:
    unit = _unit(db, TickSchema.TRADES, DAY1)
    definition = _definitions(db, DAY1, UnitState.VERIFIED)
    selection = await select_units(aconn)
    assert _ids(selection) == []
    assert _skips(selection) == {
        unit: (SkipKind.AWAITING_DEFINITIONS, "awaiting definitions")
    }
    db.execute(
        "UPDATE tick_archive_unit SET state = 'ingested' WHERE unit_id = %s",
        (definition,),
    )
    assert _ids(await select_units(aconn)) == [unit]


async def test_a_trades_unit_is_outranked_by_a_current_tbbo_unit(
    db: Any, aconn: AConn
) -> None:
    _definitions(db, DAY1)
    trades = _unit(db, TickSchema.TRADES, DAY1)
    tbbo = _unit(db, TickSchema.TBBO, DAY1, UnitState.REQUESTED)
    selection = await select_units(aconn)
    assert _skips(selection) == {
        trades: (SkipKind.OUTRANKED, f"outranked by unit {tbbo}")
    }
    db.execute(
        "UPDATE tick_archive_unit SET state = 'verified', file_path = %s,"
        " file_size_bytes = 1, file_sha256 = %s WHERE unit_id = %s",
        (FILE_COLUMNS["file_path"], "0" * 64, tbbo),
    )
    assert _ids(await select_units(aconn)) == [tbbo]


async def test_superseded_reopened_and_exhausted_units_are_not_selected(
    db: Any, aconn: AConn
) -> None:
    _definitions(db, DAY1)
    keeper = _unit(db, TickSchema.TRADES, DAY1)
    superseded = _unit(db, TickSchema.TRADES, DAY1, superseded_by_unit_id=keeper)
    reopened = _unit(
        db,
        TickSchema.TRADES,
        DAY1,
        UnitState.DELIVERED,
        fetch_status=FetchStatus.PROVIDER_HOLE.value,
        failure_reason=PROVIDER_HOLE_REASON,
        reopened_at=AT,
    )
    exhausted = _unit(
        db,
        TickSchema.TRADES,
        DAY1,
        fetch_status=FetchStatus.RETRY_EXHAUSTED.value,
        failure_reason="counts: provider 1, decoded 2, stored 0",
    )
    assert _ids(await select_units(aconn)) == [keeper]
    named = _skips(await select_units(aconn, [superseded, reopened, exhausted]))
    assert named[superseded] == (
        SkipKind.NOT_SELECTABLE,
        f"superseded by unit {keeper}",
    )
    assert named[reopened][0] is SkipKind.NOT_SELECTABLE
    assert named[exhausted] == (
        SkipKind.NOT_SELECTABLE,
        "fetch_status is RETRY_EXHAUSTED; reset it first",
    )


async def test_a_named_unit_narrows_and_never_overrides(db: Any, aconn: AConn) -> None:
    _definitions(db, DAY1)
    first = _unit(db, TickSchema.TRADES, DAY1)
    second = _unit(db, TickSchema.TRADES, DAY2)
    definition = _definitions(db, DAY2, UnitState.VERIFIED)
    only = await select_units(aconn, [first])
    assert (_ids(only), _skips(only)) == ([first], {})
    named = await select_units(aconn, [second, definition, 999_999])
    assert _ids(named) == []
    assert _skips(named) == {
        second: (SkipKind.AWAITING_DEFINITIONS, "awaiting definitions"),
        definition: (
            SkipKind.NOT_SELECTABLE,
            "schema definition is not a stored tier",
        ),
        999_999: (SkipKind.NOT_SELECTABLE, "no such unit"),
    }


async def test_order_is_day_then_unit_id(db: Any, aconn: AConn) -> None:
    _definitions(db, DAY1)
    _definitions(db, DAY2)
    later = _unit(db, TickSchema.TRADES, DAY2)
    early_a = _unit(db, TickSchema.TRADES, DAY1)
    early_b = _unit(db, TickSchema.TRADES, DAY1)
    assert _ids(await select_units(aconn)) == [early_a, early_b, later]
