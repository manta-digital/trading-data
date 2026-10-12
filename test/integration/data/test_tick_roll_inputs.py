"""``roll_inputs`` on a scratch tick database (slice 229, Technical Requirements).

Current units only, outrights only, an unknown product refused, and the held
bounds on an empty and a seeded shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import psycopg
import pytest
from tick_support.roll_seed import (
    SPREAD_ID,
    expiring,
    seed_definitions,
    seed_ledger,
    seed_tier_days,
)
from tick_support.rows import (
    FILE_COLUMNS,
    insert_ledger_row,
    insert_request,
    insert_unit,
)
from tick_support.runs import connect

from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema, UnitState
from manta_trading.data.tick.roll_inputs import (
    definitions_as_of,
    held_session_bounds,
    outright_definitions,
    session_volumes,
)
from manta_trading.data.tick.roll_types import RollInputError
from manta_trading.data.tick.status_reads import Shape

AConn = psycopg.AsyncConnection[Any]
SHAPE = Shape(CME_DATASET, ("ES.FUT",), SType.PARENT)
D1, D2 = date(2024, 9, 3), date(2024, 9, 4)
U = expiring(118, "ESU4", date(2022, 6, 17), date(2024, 9, 20))
Z = expiring(183748, "ESZ4", date(2021, 6, 4), date(2024, 12, 20))


@pytest.fixture
async def aconn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with connect(migrated_tick_db) as conn:
        yield conn


@pytest.fixture
def seeded(migrated_tick_db: str) -> dict[date, int]:
    seed_definitions(migrated_tick_db, [Z, U], date(2024, 8, 30))
    units = seed_tier_days(migrated_tick_db, [D1, D2])
    seed_ledger(
        migrated_tick_db,
        units,
        {
            (D1, U.instrument_id): 500,
            (D1, Z.instrument_id): 300,
            (D1, SPREAD_ID): 9,
            (D2, U.instrument_id): 400,
        },
    )
    return units


async def test_outright_definitions_are_ordered_by_expiry_without_spreads(
    seeded: dict[date, int], aconn: AConn
) -> None:
    defs = await outright_definitions(aconn, "ES")
    assert [d.raw_symbol for d in defs] == ["ESU4", "ESZ4"]
    assert SPREAD_ID not in {d.instrument_id for d in defs}


async def test_unknown_product_is_an_error_not_empty(
    seeded: dict[date, int], aconn: AConn
) -> None:
    with pytest.raises(RollInputError, match="product GC has no outright"):
        await outright_definitions(aconn, "GC")


async def test_volumes_are_outrights_over_current_units(
    seeded: dict[date, int], migrated_tick_db: str, aconn: AConn
) -> None:
    rows = await session_volumes(aconn, SHAPE, D1, D2)
    assert {(r.session_date, r.instrument_id, r.volume, r.unit_id) for r in rows} == {
        (D1, U.instrument_id, 500, seeded[D1]),
        (D1, Z.instrument_id, 300, seeded[D1]),
        (D2, U.instrument_id, 400, seeded[D2]),
    }
    # A tbbo unit supersedes D1's trades unit: only its rows count now.
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.TBBO.value)
        tbbo = insert_unit(
            conn, request, unit_date=D1, state=UnitState.INGESTED.value, **FILE_COLUMNS
        )
        conn.execute(
            "UPDATE tick_archive_unit SET superseded_by_unit_id = %s"
            " WHERE unit_id = %s",
            (tbbo, seeded[D1]),
        )
        insert_ledger_row(
            conn, tbbo, instrument_id=U.instrument_id, session_date=D1, volume=7
        )
    after = await session_volumes(aconn, SHAPE, D1, D1)
    assert [(r.instrument_id, r.volume, r.unit_id) for r in after] == [
        (U.instrument_id, 7, tbbo)
    ]


async def test_held_bounds_empty_then_seeded(
    migrated_tick_db: str, aconn: AConn
) -> None:
    assert await held_session_bounds(aconn, SHAPE) is None
    units = seed_tier_days(migrated_tick_db, [D1, D2])
    seed_ledger(migrated_tick_db, units, {(D1, 1): 1, (D2, 1): 1})
    assert await held_session_bounds(aconn, SHAPE) == (D1, D2)


async def test_definitions_as_of_is_the_latest_definition_unit(
    seeded: dict[date, int], migrated_tick_db: str, aconn: AConn
) -> None:
    assert await definitions_as_of(aconn, SHAPE) == date(2024, 8, 30)
    seed_definitions(
        migrated_tick_db,
        [expiring(5002, "ESH5", date(2022, 12, 16), date(2025, 3, 21))],
        date(2024, 12, 8),
        spread=False,
    )
    assert await definitions_as_of(aconn, SHAPE) == date(2024, 12, 8)
