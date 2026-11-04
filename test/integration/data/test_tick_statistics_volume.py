"""The cross-tier volume check (slice 303, D8): traded volume from the tick
ledger or the bar ledger against the resolved cleared volume.

Everything is seeded directly (units, definitions, ledger and statistics rows);
the reads touch only those and the calendar. Session 2024-12-03's statistics
window is 12-02 to 12-04; its own days are 12-02 and 12-03.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import psycopg
from tick_support.rows import (
    insert_bar_ledger_row,
    insert_day_condition,
    insert_ledger_row,
    insert_statistics,
)
from tick_support.runs import connect
from tick_support.statistics_seed import seed_outright, seed_unit, seed_units
from tick_support.universe import UNTIERED

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.constants import (
    CME_DATASET,
    SPREAD_CLASS,
    SType,
    TickSchema,
)
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_constants import StatType
from manta_trading.data.tick.statistics_reads import (
    VolumeClass,
    VolumeVerdict,
    checkable_sessions,
    volume_check,
)
from manta_trading.data.tick.status_reads import Shape, calendar_sessions

SESSION = date(2024, 12, 3)
WINDOW = [date(2024, 12, 2), date(2024, 12, 3), date(2024, 12, 4)]
OWN_DAYS = WINDOW[:2]
SHAPE = Shape(CME_DATASET, ("ES.FUT",), SType.PARENT)
ES = UNTIERED[0]
EXACT, SHORT, EXCESS, SILENT, UNCLEARED, EXPIRING, SPREAD = range(
    42_000_001, 42_000_008
)
CLEARED_VOLUME = int(StatType.CLEARED_VOLUME)


def _session(url: str, day: date = SESSION) -> Session:
    (found,) = [
        s
        for s in calendar_sessions(
            url,
            "CME_EQUITY",
            datetime.combine(day, time(), UTC),
            datetime.combine(day + timedelta(days=1), time(), UTC),
        )
        if s.session_date == day
    ]
    return found


def _seed_definitions(conn: psycopg.Connection[Any]) -> None:
    definition = seed_unit(conn, TickSchema.DEFINITION, SESSION)
    for instrument in (EXACT, SHORT, EXCESS, SILENT, UNCLEARED):
        seed_outright(conn, definition, instrument)
    seed_outright(
        conn,
        definition,
        EXPIRING,
        expiration_ns=int(datetime(2024, 12, 3, 12, tzinfo=UTC).timestamp())
        * 1_000_000_000,
    )
    seed_outright(conn, definition, SPREAD, instrument_class=SPREAD_CLASS)


#: instrument -> (cleared volume C, tick-tier volume, bar volume).
VOLUMES = {
    EXACT: (100, 100, 100),
    SHORT: (200, 150, 120),
    EXCESS: (50, 60, 50),
    SILENT: (30, 0, 30),
    EXPIRING: (10, 10, 10),
    SPREAD: (10, 10, 10),
}


def _seed_volumes(conn: psycopg.Connection[Any]) -> None:
    statistics = seed_unit(conn, TickSchema.STATISTICS, SESSION)
    ticks = seed_unit(conn, TickSchema.TBBO, SESSION)
    bars = seed_unit(conn, TickSchema.OHLCV_1M, SESSION)
    for sequence, (instrument, (cleared, tick, bar)) in enumerate(VOLUMES.items()):
        insert_statistics(
            conn,
            statistics,
            instrument_id=instrument,
            stat_type=CLEARED_VOLUME,
            quantity=cleared,
            price=None,
            session_date=SESSION,
            sequence=sequence,
        )
        insert_ledger_row(
            conn, ticks, instrument_id=instrument, session_date=SESSION, volume=tick
        )
        insert_bar_ledger_row(
            conn, bars, instrument_id=instrument, session_date=SESSION, volume=bar
        )
    # No cleared volume at all for UNCLEARED, though it traded.
    insert_ledger_row(
        conn, ticks, instrument_id=UNCLEARED, session_date=SESSION, volume=7
    )


async def _check(
    tick_url: str, calendar_url: str, family: SchemaFamily
) -> dict[int, VolumeVerdict]:
    async with connect(tick_url) as conn:
        found = await volume_check(conn, SHAPE, family, "ES", [_session(calendar_url)])
    return {v.instrument_id: v for v in found}


async def test_the_tick_tier_is_checked_per_listed_outright(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        _seed_definitions(conn)
        _seed_volumes(conn)
    verdicts = await _check(
        migrated_tick_db, session_migrated_db, SchemaFamily.TICK_TIER
    )
    assert {i: v.verdict for i, v in verdicts.items()} == {
        EXACT: VolumeClass.AGREE,
        SHORT: VolumeClass.UNDER,
        EXCESS: VolumeClass.OVER,
        SILENT: VolumeClass.NO_TRADES,
    }
    assert verdicts[SHORT].ratio == 0.75
    assert (verdicts[SHORT].cleared, verdicts[SHORT].traded) == (200, 150)
    # A contract expiring inside the session, a spread and a contract with no
    # cleared volume are not compared.
    assert {EXPIRING, SPREAD, UNCLEARED}.isdisjoint(verdicts)


async def test_the_bar_family_is_checked_against_its_own_ledger(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        _seed_definitions(conn)
        _seed_volumes(conn)
    ticks = await _check(migrated_tick_db, session_migrated_db, SchemaFamily.TICK_TIER)
    bars = await _check(migrated_tick_db, session_migrated_db, SchemaFamily.BAR_1M)
    assert {v.family for v in bars.values()} == {SchemaFamily.BAR_1M}
    assert (bars[SHORT].traded, ticks[SHORT].traded) == (120, 150)
    assert bars[SILENT].verdict is VolumeClass.AGREE  # bars saw what ticks did not
    assert ticks[SILENT].verdict is VolumeClass.NO_TRADES


async def test_only_a_volume_family_can_be_checked(migrated_tick_db: str) -> None:
    async with connect(migrated_tick_db) as conn:
        for family in (SchemaFamily.STATISTICS, SchemaFamily.DEFINITION):
            try:
                await volume_check(conn, SHAPE, family, "ES", [])
            except ValueError as error:
                assert "no traded volume" in str(error)
            else:
                raise AssertionError(f"{family} was accepted")


async def test_a_session_incomplete_in_either_family_is_skipped(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        seed_units(conn, TickSchema.STATISTICS, WINDOW)
        seed_units(conn, TickSchema.TBBO, OWN_DAYS)
        seed_units(conn, TickSchema.OHLCV_1M, OWN_DAYS[:1])  # 12-03 bars not loaded
        for day in WINDOW:
            insert_day_condition(conn, condition_date=day, condition="available")
    session = _session(session_migrated_db)
    async with connect(migrated_tick_db) as conn:
        found = {
            family: await checkable_sessions(
                conn, session_migrated_db, ES, [session], family
            )
            for family in (SchemaFamily.TICK_TIER, SchemaFamily.BAR_1M)
        }
    assert found == {SchemaFamily.TICK_TIER: [session], SchemaFamily.BAR_1M: []}
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        # Statistics' window is not whole without its last day either.
        conn.execute(
            "UPDATE tick_archive_unit SET state = 'verified' WHERE unit_date = %s"
            " AND request_id IN (SELECT request_id FROM tick_request"
            " WHERE schema = 'statistics')",
            (WINDOW[2],),
        )
    async with connect(migrated_tick_db) as conn:
        again = await checkable_sessions(
            conn, session_migrated_db, ES, [session], SchemaFamily.TICK_TIER
        )
    assert again == []
