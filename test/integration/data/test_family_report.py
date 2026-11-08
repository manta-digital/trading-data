"""``family_reports``: one report per wanted family (slice 305, D3).

One product wants the tick tier, bars and statistics. Units and ledger rows are
seeded directly: the report reads only the manifest, the ledgers, the
conditions, the edge and the calendar.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from tick_support.rows import (
    insert_bar_ledger_row,
    insert_dataset_edge,
    insert_day_condition,
    insert_ledger_row,
    insert_statistics_ledger_row,
)
from tick_support.runs import connect
from tick_support.statistics_seed import seed_unit
from tick_support.universe import UNTIERED

from manta_trading.data.tick.constants import CME_DATASET, TickSchema
from manta_trading.data.tick.family_report import (
    FamilyStatus,
    StatisticsFamilyStatus,
    family_reports,
)
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import dataset_edge
from manta_trading.data.tick.universe import HistoryWant

DAY = date(2024, 12, 3)
ES = UNTIERED[0]
WANTS_ALL = replace(
    ES,
    tier=TickSchema.TBBO,
    start=DAY,
    end=date(2024, 12, 4),
    history=(
        HistoryWant(TickSchema.OHLCV_1M, DAY, date(2024, 12, 4)),
        HistoryWant(TickSchema.STATISTICS, DAY, date(2024, 12, 5)),
    ),
)
WITHOUT_STATISTICS = replace(WANTS_ALL, history=WANTS_ALL.history[:1])


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


async def _reports(url: str, calendar_url: str, entry: Any) -> tuple[FamilyStatus, ...]:
    async with connect(url) as conn:
        edge = await dataset_edge(conn, CME_DATASET)
        return await family_reports(conn, calendar_url, entry, edge, cross_tier=False)


def _seed(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        tick = seed_unit(conn, TickSchema.TBBO, DAY)
        bars = seed_unit(conn, TickSchema.OHLCV_1M, DAY)
        stats = seed_unit(conn, TickSchema.STATISTICS, DAY)
        insert_ledger_row(conn, tick, session_date=DAY, record_count=7)
        insert_bar_ledger_row(conn, bars, session_date=DAY, record_count=5)
        insert_statistics_ledger_row(conn, stats, session_date=DAY, record_count=3)
        insert_dataset_edge(conn, available_end=datetime(2030, 1, 1, tzinfo=UTC))
        insert_day_condition(conn, condition_date=DAY, condition="available")


async def test_a_product_gets_one_report_per_wanted_family_in_want_order(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    _seed(migrated_tick_db)
    tier, bars, statistics = await _reports(
        migrated_tick_db, session_migrated_db, WANTS_ALL
    )
    assert [r.family for r in (tier, bars, statistics)] == [
        SchemaFamily.TICK_TIER.value,
        SchemaFamily.BAR_1M.value,
        SchemaFamily.STATISTICS.value,
    ]
    # Each family's edge is its own want end, not the dataset's (2030).
    assert tier.source_edge == bars.source_edge == _midnight(date(2024, 12, 4))
    assert statistics.source_edge == _midnight(date(2024, 12, 5))
    assert (tier.records, bars.records, statistics.records) == (7, 5, 3)
    assert not any(r.cross_tier for r in (tier, bars, statistics))
    assert isinstance(statistics, StatisticsFamilyStatus)
    assert not isinstance(tier, StatisticsFamilyStatus)


async def test_load_complete_only_marks_volume_families_without_a_statistics_want(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    _seed(migrated_tick_db)
    checked = await _reports(migrated_tick_db, session_migrated_db, WANTS_ALL)
    assert [r.load_complete_only for r in checked] == [False, False, False]
    alone = await _reports(migrated_tick_db, session_migrated_db, WITHOUT_STATISTICS)
    assert [(r.family, r.load_complete_only) for r in alone] == [
        (SchemaFamily.TICK_TIER.value, True),
        (SchemaFamily.BAR_1M.value, True),
    ]


async def test_no_dataset_edge_gives_no_source_edge_and_no_verdict(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    reports = await _reports(migrated_tick_db, session_migrated_db, WITHOUT_STATISTICS)
    assert [(r.source_edge, r.caught_up) for r in reports] == [(None, None)] * 2
