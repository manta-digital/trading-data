"""``family_reports``: one report per wanted family (slice 305, D3).

One product wants the tick tier, bars and statistics. Units and ledger rows are
seeded directly: the report reads only the manifest, the ledgers, the
conditions, the edge and the calendar.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
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

from manta_trading.cli.commands.tick_status_render import _product_lines
from manta_trading.data.tick import cross_tier_checks
from manta_trading.data.tick.constants import CME_DATASET, TickSchema
from manta_trading.data.tick.family_report import (
    LOAD_COMPLETE_ONLY,
    FamilyStatus,
    ReadProgress,
    StatisticsFamilyStatus,
    family_reports,
    futures_completeness,
)
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import dataset_edge
from manta_trading.data.tick.tick_status_build import build_status
from manta_trading.data.tick.universe import HistoryWant

DAY = date(2024, 12, 3)
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
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


class _VolumeSpy:
    """Counts ``volume_check`` calls and answers like a session with no
    comparable contract."""

    def __init__(self) -> None:
        self.families: list[SchemaFamily] = []

    async def __call__(
        self, conn: Any, shape: Any, family: SchemaFamily, *rest: Any
    ) -> tuple[()]:
        self.families.append(family)
        return ()


@pytest.fixture
def volume_spy(monkeypatch: pytest.MonkeyPatch) -> _VolumeSpy:
    spy = _VolumeSpy()
    monkeypatch.setattr(cross_tier_checks, "volume_check", spy)
    return spy


async def test_cross_tier_false_leaves_results_empty_and_never_checks_volume(
    migrated_tick_db: str, session_migrated_db: str, volume_spy: _VolumeSpy
) -> None:
    _seed(migrated_tick_db)
    async with connect(migrated_tick_db) as conn:
        edge = await dataset_edge(conn, CME_DATASET)
        off = await family_reports(
            conn, session_migrated_db, WANTS_ALL, edge, cross_tier=False
        )
        assert volume_spy.families == []
        on = await family_reports(
            conn, session_migrated_db, WANTS_ALL, edge, cross_tier=True
        )
    assert not any(r.cross_tier for r in off)
    assert volume_spy.families == [SchemaFamily.TICK_TIER, SchemaFamily.BAR_1M]
    tier, bars, statistics = on
    assert [len(r.cross_tier) for r in (tier, bars, statistics)] == [1, 1, 0]


async def test_status_prints_the_counts_the_inactive_check_and_the_label(
    migrated_tick_db: str, session_migrated_db: str, volume_spy: _VolumeSpy
) -> None:
    _seed(migrated_tick_db)
    async with connect(migrated_tick_db) as conn:
        checked = await build_status(
            conn, session_migrated_db, [WANTS_ALL], NOW, tick_url=migrated_tick_db
        )
        alone = await build_status(
            conn,
            session_migrated_db,
            [WITHOUT_STATISTICS],
            NOW,
            tick_url=migrated_tick_db,
        )
    (product,) = checked.products
    text = "\n".join(_product_lines(product))
    assert "traded_vs_cleared (tick_tier): agree 0   under 0   over 0" in text
    assert "traded_vs_cleared (bar_1m): agree 0" in text
    assert (
        "cross-tier ohlcv_1s_minute_sum: not active: needs the ohlcv-1s family" in text
    )
    body = json.loads(json.dumps(checked.to_dict()))
    assert body["cross_tier_not_active"] == {
        "ohlcv_1s_minute_sum": "needs the ohlcv-1s family"
    }
    tier_json = body["products"][0]["families"][0]
    assert tier_json["cross_tier"] == [
        {
            "check": "traded_vs_cleared",
            "family": "tick_tier",
            "agree": 0,
            "under": 0,
            "over": 0,
            "no_trades": 0,
        }
    ]
    assert tier_json["load_complete_only"] is False
    (bare,) = alone.products
    assert LOAD_COMPLETE_ONLY in "\n".join(_product_lines(bare))
    assert [f.load_complete_only for f in bare.families] == [True, True]


async def test_a_product_that_wants_nothing_still_reports_its_tick_tier(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    """Accounting's line per product (230): only what units touch, no range."""
    _seed(migrated_tick_db)
    async with connect(migrated_tick_db) as conn:
        found = await futures_completeness(
            conn, session_migrated_db, [ES], ReadProgress(), cross_tier=True
        )
    (tier,) = found[ES.product]
    assert (tier.family, tier.schema) == (SchemaFamily.TICK_TIER.value, None)
    assert (tier.wanted_start, tier.caught_up, tier.load_complete_only) == (
        None,
        None,
        False,
    )
    assert tier.records == 7  # the units it holds are still counted
