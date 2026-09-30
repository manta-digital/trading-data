"""Status and coverage reads on seeded rows (slice 225, TD9, TD10)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from tick_support.rows import (
    FILE_COLUMNS,
    insert_dataset_edge,
    insert_day_condition,
    insert_definition,
    insert_ledger_row,
    insert_request,
    insert_trade,
    insert_unit,
)
from tick_support.runs import connect

from manta_trading.data.tick.constants import (
    CME_DATASET,
    DatasetCondition,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.status_reads import (
    Shape,
    conditions,
    contract_lines,
    dataset_edge,
    ledger_totals,
    raw_counts,
    shape_units,
)

AConn = psycopg.AsyncConnection[Any]
SHAPE = Shape(CME_DATASET, ("ES.FUT",), SType.PARENT)
DAY = date(2024, 9, 3)
NS = 1_000_000_000
#: 2024-09-03 00:00 UTC; the session dated 09-03 opened 09-02 22:00 UTC.
MIDNIGHT = int(datetime(2024, 9, 3, tzinfo=UTC).timestamp()) * NS
S03 = (date(2024, 9, 3), MIDNIGHT - 2 * 3600 * NS, MIDNIGHT + 21 * 3600 * NS)
S04 = (date(2024, 9, 4), MIDNIGHT + 22 * 3600 * NS, MIDNIGHT + 45 * 3600 * NS)


@pytest.fixture
def db(migrated_tick_db: str) -> Any:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def aconn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with connect(migrated_tick_db) as conn:
        yield conn


def _unit(db: Any, schema: TickSchema = TickSchema.TRADES, **unit: Any) -> int:
    request = insert_request(db, schema=schema.value)
    return insert_unit(
        db, request, state=UnitState.INGESTED.value, **FILE_COLUMNS | unit
    )


async def test_units_and_ledger_exclude_superseded_units(db: Any, aconn: AConn) -> None:
    tbbo = _unit(db, TickSchema.TBBO)
    trades = _unit(db, superseded_by_unit_id=tbbo)
    insert_ledger_row(db, tbbo, record_count=5)
    insert_ledger_row(db, trades, record_count=7)
    units = await shape_units(aconn, SHAPE)
    assert [u.unit_id for u in units[DAY]] == [tbbo]
    assert await ledger_totals(aconn, SHAPE, DAY, DAY) == {(42_035_063, DAY): 5}
    assert await ledger_totals(aconn, SHAPE, date(2024, 9, 4), date(2024, 9, 5)) == {}


async def test_conditions_and_edge_return_the_seeded_values(
    db: Any, aconn: AConn
) -> None:
    observed = datetime(2026, 9, 30, 8, tzinfo=UTC)
    insert_dataset_edge(db, observed_at=observed)
    insert_day_condition(db, condition=DatasetCondition.DEGRADED.value)
    edge = await dataset_edge(aconn, CME_DATASET)
    assert edge is not None and edge.observed_at == observed
    assert await dataset_edge(aconn, "XNAS.ITCH") is None
    assert await conditions(aconn, CME_DATASET, DAY, DAY) == {
        DAY: DatasetCondition.DEGRADED
    }


async def test_contract_lines_join_the_latest_definition(db: Any, aconn: AConn) -> None:
    unit = _unit(db)
    insert_definition(db, unit)
    insert_ledger_row(db, unit, record_count=4, volume=9)
    insert_ledger_row(
        db,
        unit,
        instrument_id=5,
        record_count=0,
        volume=0,
        first_event_ns=None,
        last_event_ns=None,
    )
    (line,) = await contract_lines(aconn, SHAPE)
    assert (line.instrument_id, line.raw_symbol, line.records, line.volume) == (
        42_035_063,
        "ESZ4",
        4,
        9,
    )
    assert (line.sessions, line.first_session, line.last_session) == (1, DAY, DAY)


async def test_raw_counts_assign_rows_to_sessions_across_midnight(
    db: Any, aconn: AConn
) -> None:
    unit = _unit(db)
    for sequence, ts in enumerate(
        (
            MIDNIGHT - 3600 * NS,  # 09-02 23:00: session 09-03
            MIDNIGHT + 3600 * NS,  # 09-03 01:00: session 09-03
            MIDNIGHT + 23 * 3600 * NS,  # 09-03 23:00: session 09-04
            MIDNIGHT + 50 * 3600 * NS,  # past the range: ignored
        )
    ):
        insert_trade(db, unit, ts_event=ts, ts_recv=ts + 1, sequence=sequence)
    counts = await raw_counts(aconn, [S03, S04])
    assert counts == {(42_035_063, S03[0]): 2, (42_035_063, S04[0]): 1}
    assert await raw_counts(aconn, []) == {}
