"""Edge-bounded judging of ``caught_up`` (slice 305 D2) and the real-calendar edge.

Statistics sessions are judged only once their publication window has closed:
session D's window runs through the close of the session after D, so a session
whose window is still open is ``pending`` and neither helps nor hurts
``caught_up``. Each family is bounded by its own want end, never another's.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from typing import Any

import psycopg
from tick_support.rows import insert_dataset_edge, insert_day_condition
from tick_support.runs import connect
from tick_support.statistics_seed import seed_units
from tick_support.universe import UNTIERED

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.family_edges import family_source_edge
from manta_trading.data.tick.family_report import FamilyStatus
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import Edge
from manta_trading.data.tick.tick_status_build import build_status
from manta_trading.data.tick.universe import HistoryWant

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
ES = UNTIERED[0]


def _entry(
    stats_start: date, stats_end: date, *, tick_end: date = date(2024, 12, 31)
) -> Any:
    return replace(
        ES,
        tier=TickSchema.TBBO,
        start=date(2024, 12, 3),
        end=tick_end,
        history=(HistoryWant(TickSchema.STATISTICS, stats_start, stats_end),),
    )


def _seed(
    url: str, loaded: list[date], conditions: dict[date, str], edge_end: datetime
) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        seed_units(conn, TickSchema.STATISTICS, loaded)
        for day, condition in conditions.items():
            insert_day_condition(conn, condition_date=day, condition=condition)
        insert_dataset_edge(conn, available_end=edge_end)


async def _statistics(url: str, calendar_url: str, entry: Any) -> FamilyStatus:
    async with connect(url) as conn:
        status = await build_status(conn, calendar_url, [entry], NOW, tick_url=url)
    (product,) = status.products
    (section,) = [f for f in product.families if f.family == SchemaFamily.STATISTICS]
    return section


async def test_a_session_whose_publication_window_is_open_is_not_judged(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    days = [date(2024, 12, d) for d in (2, 3, 4)]
    # The dataset ends Thursday 12-05 12:00Z: Tuesday's window (through
    # Wednesday's close) has closed, Wednesday's (through Thursday's) has not.
    conditions = {day: "available" for day in days} | {date(2024, 12, 5): "pending"}
    _seed(migrated_tick_db, days, conditions, datetime(2024, 12, 5, 12, tzinfo=UTC))
    entry = _entry(date(2024, 12, 3), date(2024, 12, 5))
    section = await _statistics(migrated_tick_db, session_migrated_db, entry)
    assert section.buckets["complete"] == 1  # Tuesday
    # Wednesday and Thursday (it opens before the want ends): windows still open.
    assert section.buckets["pending"] == 2
    assert section.caught_up is True


async def test_a_statistics_session_missing_in_2025_is_judged_past_the_tick_want_end(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    """Production shape: the tick want ends 2024-12-31, the statistics want runs
    into 2025 and later. Bounding statistics by the tick want would leave this
    session out of range and report caught up."""
    days = [date(2025, 3, d) for d in (2, 3, 4, 6, 7, 9, 10)]  # no 03-05
    conditions = {day: "available" for day in [*days, date(2025, 3, 5)]}
    _seed(migrated_tick_db, days, conditions, datetime(2026, 11, 6, tzinfo=UTC))
    entry = _entry(date(2025, 3, 3), date(2025, 3, 7))
    section = await _statistics(migrated_tick_db, session_migrated_db, entry)
    assert section.buckets["missing"] >= 1
    assert section.caught_up is False


async def test_a_dataset_edge_in_the_future_of_the_calendar_still_gives_an_edge(
    session_migrated_db: str,
) -> None:
    far = Edge(datetime(2030, 1, 1, tzinfo=UTC), NOW)
    edge = await family_source_edge(
        session_migrated_db, ES, SchemaFamily.STATISTICS, None, far
    )
    # The calendar ends 2027-12-31: the edge is its last closed window, not an error.
    assert edge is not None
    assert datetime(2027, 12, 20, tzinfo=UTC) < edge < datetime(2028, 1, 1, tzinfo=UTC)


async def test_family_source_edge_on_the_real_calendar(
    session_migrated_db: str,
) -> None:
    # Friday 2026-11-06 00:00Z: Thursday has closed (Thu 22:00Z), so Wednesday's
    # window (through Thursday's close) has closed and Thursday's has not.
    edge = Edge(datetime.combine(date(2026, 11, 6), time(), UTC), NOW)
    found = await family_source_edge(
        session_migrated_db, ES, SchemaFamily.STATISTICS, None, edge
    )
    assert found == datetime(2026, 11, 4, 22, tzinfo=UTC)
    # A family with no lag reads no calendar, so it works with a bad URL.
    tick = await family_source_edge(
        "postgresql://unused", ES, SchemaFamily.TICK_TIER, date(2024, 12, 31), edge
    )
    assert tick == datetime(2024, 12, 31, tzinfo=UTC)
