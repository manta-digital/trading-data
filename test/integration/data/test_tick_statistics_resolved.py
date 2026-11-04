"""``statistics_reads.resolved``: one value per contract, statistic and session
(slice 303, D5)."""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from tick_support.rows import (
    FILE_COLUMNS,
    insert_request,
    insert_statistics,
    insert_unit,
)
from tick_support.runs import connect

from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema, UnitState
from manta_trading.data.tick.statistics_constants import StatType, StatUpdateAction
from manta_trading.data.tick.statistics_reads import ResolvedStatistic, resolved
from manta_trading.data.tick.status_reads import Shape

SHAPE = Shape(CME_DATASET, ("ES.FUT",), SType.PARENT)
SESSION = date(2024, 9, 3)
INSTRUMENT = 42_035_063
SETTLEMENT = StatType.SETTLEMENT_PRICE
ALL_TYPES = list(StatType)
FIRST, LAST = date(2024, 9, 1), date(2024, 9, 30)
T0 = 1_725_321_600_000_000_000  # 2024-09-03T00:00Z


def _unit(conn: psycopg.Connection[Any], **request: Any) -> int:
    return insert_unit(
        conn,
        insert_request(conn, schema=TickSchema.STATISTICS.value, **request),
        state=UnitState.INGESTED.value,
        **FILE_COLUMNS,
    )


async def _resolved(
    url: str, stat_types: list[StatType] = ALL_TYPES, **window: date
) -> tuple[ResolvedStatistic, ...]:
    async with connect(url) as conn:
        return await resolved(
            conn,
            SHAPE,
            stat_types,
            window.get("first", FIRST),
            window.get("last", LAST),
        )


def _prices(rows: tuple[ResolvedStatistic, ...]) -> list[int | None]:
    return [row.price for row in rows]


async def test_the_latest_receive_time_wins_across_units(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        early, late = _unit(conn), _unit(conn)
        insert_statistics(conn, late, ts_recv=T0 + 10, price=2)
        insert_statistics(conn, early, ts_recv=T0 + 5, price=1)
    (row,) = await _resolved(migrated_tick_db)
    assert row == ResolvedStatistic(INSTRUMENT, SETTLEMENT, SESSION, 2, None, late)


async def test_a_tie_on_receive_time_goes_to_sequence_then_ordinal(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = _unit(conn)
        insert_statistics(conn, unit, ts_recv=T0, sequence=1, price=1)
        insert_statistics(conn, unit, ts_recv=T0, sequence=2, price=2)
        insert_statistics(conn, unit, ts_recv=T0, sequence=2, record_ordinal=1, price=3)
    assert _prices(await _resolved(migrated_tick_db)) == [3]


async def test_a_later_delete_removes_the_value_and_a_new_after_it_restores(
    migrated_tick_db: str,
) -> None:
    delete = int(StatUpdateAction.DELETE)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        first, second, third = _unit(conn), _unit(conn), _unit(conn)
        insert_statistics(conn, first, ts_recv=T0, price=1)
        insert_statistics(conn, second, ts_recv=T0 + 1, update_action=delete)
    assert await _resolved(migrated_tick_db) == ()
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        insert_statistics(conn, third, ts_recv=T0 + 2, price=3)
    assert _prices(await _resolved(migrated_tick_db)) == [3]


async def test_superseded_and_reopened_units_are_ignored(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        current, superseded, reopened = _unit(conn), _unit(conn), _unit(conn)
        insert_statistics(conn, current, ts_recv=T0, price=1)
        insert_statistics(conn, superseded, ts_recv=T0 + 1, price=2)
        insert_statistics(conn, reopened, ts_recv=T0 + 2, price=3)
        conn.execute(
            "UPDATE tick_archive_unit SET superseded_by_unit_id = %s"
            " WHERE unit_id = %s",
            (current, superseded),
        )
        # A reopened unit holds no file: it is back at ``requested``.
        conn.execute(
            "UPDATE tick_archive_unit SET reopened_at = now(), state = %s,"
            " file_path = NULL, file_size_bytes = NULL, file_sha256 = NULL"
            " WHERE unit_id = %s",
            (UnitState.REQUESTED.value, reopened),
        )
    (row,) = await _resolved(migrated_tick_db)
    assert (row.price, row.unit_id) == (1, current)


async def test_records_with_no_session_never_appear(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = _unit(conn)
        insert_statistics(
            conn, unit, ts_ref=None, session_date=None, calendar_id=None, price=1
        )
    assert await _resolved(migrated_tick_db) == ()


async def test_each_contract_statistic_and_session_has_its_own_value(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = _unit(conn)
        insert_statistics(conn, unit, price=1)
        insert_statistics(conn, unit, instrument_id=INSTRUMENT + 1, price=2)
        insert_statistics(conn, unit, stat_type=int(StatType.OPEN_INTEREST), price=3)
        insert_statistics(
            conn, unit, session_date=date(2024, 9, 4), sequence=2, price=4
        )
    rows = await _resolved(migrated_tick_db)
    assert [(r.session_date, r.instrument_id, r.stat_type, r.price) for r in rows] == [
        (SESSION, INSTRUMENT, 3, 1),
        (SESSION, INSTRUMENT, 9, 3),
        (SESSION, INSTRUMENT + 1, 3, 2),
        (date(2024, 9, 4), INSTRUMENT, 3, 4),
    ]


async def test_the_stat_types_and_session_window_filter(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = _unit(conn)
        insert_statistics(conn, unit, price=1)
        insert_statistics(conn, unit, stat_type=int(StatType.OPEN_INTEREST), price=3)
        insert_statistics(
            conn, unit, session_date=date(2024, 9, 4), sequence=2, price=4
        )
    only_interest = await _resolved(migrated_tick_db, [StatType.OPEN_INTEREST])
    assert _prices(only_interest) == [3]
    narrow = await _resolved(migrated_tick_db, first=SESSION, last=SESSION)
    assert [r.session_date for r in narrow] == [SESSION, SESSION]


async def test_only_the_shapes_own_units_count(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        mine = _unit(conn)
        other = _unit(conn, symbols=["GC.FUT"])
        insert_statistics(conn, mine, ts_recv=T0, price=1)
        insert_statistics(conn, other, ts_recv=T0 + 1, price=2)
    (row,) = await _resolved(migrated_tick_db)
    assert (row.price, row.unit_id) == (1, mine)
