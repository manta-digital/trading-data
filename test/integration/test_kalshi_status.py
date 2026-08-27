"""Integration tests: ``read_catalog_status`` on a throwaway database (slice 262).

Seeds through the Section 3 repository so rows are real served shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from kalshi_helpers import market_rows, parent_events, parent_series

from manta_trading.data.kalshi.constants import MarketStatus, Surface
from manta_trading.data.kalshi.repository import CatalogRepository
from manta_trading.data.kalshi.status import age_bucket_labels, read_catalog_status


def test_never_synced_is_none(kalshi_db: str):
    with psycopg.connect(kalshi_db) as conn:
        assert read_catalog_status(conn) is None


async def test_histogram_threshold_and_oldest(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    now = datetime.now(UTC)
    ages = {"HALF": 0.5, "THREE": 3, "TEN": 10, "FORTY": 40}
    base = market_rows("markets_open")[0]
    markets = [
        base.model_copy(
            update={
                "ticker": ticker,
                "status": MarketStatus.CLOSED.value,
                "close_time": now - timedelta(days=days),
            }
        )
        for ticker, days in ages.items()
    ]
    repo = CatalogRepository(kalshi_conn)
    async with repo.transaction():
        events = parent_events(markets)
        await repo.upsert_series(parent_series(events))
        await repo.upsert_events(events)
        await repo.upsert_markets(markets)
        await repo.enter_awaiting(now)
        await repo.mark_checked(["TEN"], now)
        await repo.set_last_full_sync(Surface.CATALOG, now)

    with psycopg.connect(kalshi_db) as sync_conn:
        status = read_catalog_status(sync_conn)
    assert status is not None
    assert status.last_full_sync_at == now and status.watermark_ts is None
    assert status.series == 1 and status.events == 1
    assert status.markets_by_status[MarketStatus.CLOSED] == 4
    assert status.markets_by_status[MarketStatus.ACTIVE] == 0
    assert status.awaiting.total == 4
    assert status.awaiting.age_histogram == (1, 1, 1, 1)
    assert status.awaiting.past_threshold == 2
    assert status.awaiting.oldest_ticker == "FORTY"
    assert (
        status.awaiting.oldest_age is not None and status.awaiting.oldest_age.days == 40
    )
    assert status.awaiting.checked_directly == 1
    payload = status.to_dict()
    assert age_bucket_labels() == ["<1d", "1d-7d", "7d-30d", ">30d"]
    assert payload["awaiting_age"] == {"<1d": 1, "1d-7d": 1, "7d-30d": 1, ">30d": 1}
    assert payload["awaiting_past_threshold"] == 2
    assert payload["awaiting_oldest_age_days"] == 40
    assert payload["stuck_threshold_days"] == 7


# ---------------------------------------------------------------------------
# Candle block (slice 264, Task 6.3) — every field, from persisted facts only
# ---------------------------------------------------------------------------


def test_candles_never_collected_is_none(kalshi_db: str):
    from test_kalshi_candles import RULE_C

    from manta_trading.data.kalshi.status import read_candle_status

    with psycopg.connect(kalshi_db) as conn:
        assert read_candle_status(conn, RULE_C) is None


async def test_candle_block_every_field(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    """Seeds the predicate fixture set (live), a finalized copy behind the
    cutoff, and state rows in every condition the block distinguishes."""
    from test_kalshi_candles import (
        CUTOFF,
        EVERYTHING,
        OPENED,
        RULE_C,
        fixture_markets,
    )

    from manta_trading.data.kalshi.candle_plan import period_span
    from manta_trading.data.kalshi.candle_repository import (
        CandleRepository,
        StateAdvance,
    )
    from manta_trading.data.kalshi.constants import (
        CANDLE_LAG_STALE_AFTER,
        COLLECTED_CANDLE_PERIOD,
    )
    from manta_trading.data.kalshi.status import read_candle_status

    now = datetime.now(UTC)
    span = period_span(COLLECTED_CANDLE_PERIOD)
    live, series = fixture_markets()
    # POLITICS and NULLCAT are selected live; SPORTS is excluded by rule.
    old_finalized, _ = fixture_markets(
        status=MarketStatus.FINALIZED.value,
        close_time=CUTOFF - timedelta(days=2),
        settlement_ts=CUTOFF - timedelta(days=1),
        volume_24h_fp=Decimal(0),
    )
    for market in old_finalized:
        market.ticker = f"OLD-{market.ticker}"
    recent_finalized, _ = fixture_markets(
        status=MarketStatus.FINALIZED.value,
        close_time=now - timedelta(hours=3),
        settlement_ts=now - timedelta(hours=2),
        volume_24h_fp=Decimal(0),
    )
    for market in recent_finalized:
        market.ticker = f"NEW-{market.ticker}"
    catalog = CatalogRepository(kalshi_conn)
    async with catalog.transaction():
        await catalog.upsert_series(series)
        await catalog.upsert_events(parent_events(live))
        await catalog.upsert_markets([*live, *old_finalized, *recent_finalized])
    repo = CandleRepository(kalshi_conn, RULE_C)
    fresh = now - timedelta(minutes=5)
    stale = now - CANDLE_LAG_STALE_AFTER - timedelta(minutes=1)
    new_politics = next(m for m in recent_finalized if m.ticker == "NEW-POLITICS")
    new_nullcat = next(m for m in recent_finalized if m.ticker == "NEW-NULLCAT")
    async with repo.transaction():
        await repo.advance_state(
            COLLECTED_CANDLE_PERIOD,
            [
                # open, selected, fresh — tracked, not lagging, partial history
                StateAdvance("POLITICS", fresh, OPENED + timedelta(hours=1)),
                # open, selected, stale — lagging
                StateAdvance("NULLCAT", stale, OPENED),
                # open, tracked but deselected (Sports) and stale — idle, not lagging
                StateAdvance("SPORTS", stale, OPENED),
                # closed, complete through close
                StateAdvance(
                    "NEW-POLITICS",
                    new_politics.close_time + span,
                    OPENED,
                ),
                # closed, short of close, not behind the cutoff
                StateAdvance("NEW-NULLCAT", new_nullcat.close_time - span, OPENED),
            ],
        )
        await repo.set_sync_state(now, CUTOFF)

    with psycopg.connect(kalshi_db) as conn:
        status = read_candle_status(conn, RULE_C)
    assert status is not None
    assert status.period_minutes == int(COLLECTED_CANDLE_PERIOD)
    assert status.last_phase_at == now
    assert status.cutoff_observed == CUTOFF
    assert status.rule == RULE_C
    assert status.selected_open == 2  # POLITICS, NULLCAT
    assert status.markets_tracked == 5
    assert status.open_lagging == 1  # NULLCAT; SPORTS is deselected, so idle
    assert status.open_oldest_watermark == stale
    assert status.complete_through_close == 1  # NEW-POLITICS
    assert status.closed_short_of_close == 1  # NEW-NULLCAT
    # Backlog: finalized since the cutoff, selected (ever), no state row.
    # Rule C selects NEW-POLITICS and NEW-NULLCAT, and both are tracked.
    assert status.backlog_remaining == 0
    assert status.behind_cutoff_uncollected == 2  # OLD-POLITICS, OLD-NULLCAT
    # Closed, untracked, and not selected: the five OLD-* and five NEW-*
    # markets rule C excludes (Sports, Mentions, patterns, never traded).
    assert status.closed_excluded_by_rule == 10
    assert status.partial_history == 1  # POLITICS coverage after open
    payload = status.to_dict()
    assert payload["rule"]["description"] == RULE_C.describe()
    assert payload["cutoff_observed"] == CUTOFF.isoformat()

    # Changing the rule moves the rule-dependent figures with no collection.
    with psycopg.connect(kalshi_db) as conn:
        everything = read_candle_status(conn, EVERYTHING)
    assert everything is not None
    assert everything.selected_open == 7
    assert everything.closed_excluded_by_rule == 0
    assert everything.behind_cutoff_uncollected == 7
    assert everything.backlog_remaining == 5  # the untracked NEW-* markets
    assert everything.open_lagging == 2  # SPORTS is now selected, and stale
    assert everything.markets_tracked == 5  # unchanged: a persisted fact


async def test_open_market_complete_through_close_is_not_lagging(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    """A selected market past its close but not yet finalized, whose watermark
    has reached ``close_time + period``, has nothing left to fetch: it is
    complete through close, never lagging, however old the watermark. The
    2026-08-27 rehearsal counted 409 such markets as lagging."""
    from test_kalshi_candles import CUTOFF, OPENED, RULE_C, fixture_markets

    from manta_trading.data.kalshi.candle_plan import period_span
    from manta_trading.data.kalshi.candle_repository import (
        CandleRepository,
        StateAdvance,
    )
    from manta_trading.data.kalshi.constants import (
        CANDLE_LAG_STALE_AFTER,
        COLLECTED_CANDLE_PERIOD,
    )
    from manta_trading.data.kalshi.status import read_candle_status

    now = datetime.now(UTC)
    span = period_span(COLLECTED_CANDLE_PERIOD)
    closed_at = now - CANDLE_LAG_STALE_AFTER - timedelta(hours=1)
    # Every fixture market closed an hour beyond the stale horizon and awaits
    # determination; rule C selects POLITICS and NULLCAT.
    markets, series = fixture_markets(
        status=MarketStatus.CLOSED.value, close_time=closed_at
    )
    catalog = CatalogRepository(kalshi_conn)
    async with catalog.transaction():
        await catalog.upsert_series(series)
        await catalog.upsert_events(parent_events(markets))
        await catalog.upsert_markets(markets)
    repo = CandleRepository(kalshi_conn, RULE_C)
    async with repo.transaction():
        await repo.advance_state(
            COLLECTED_CANDLE_PERIOD,
            [
                # complete through close: not lagging
                StateAdvance("POLITICS", closed_at + span, OPENED),
                # one period short of close, and stale: lagging
                StateAdvance("NULLCAT", closed_at - span, OPENED),
            ],
        )
        await repo.set_sync_state(now, CUTOFF)

    with psycopg.connect(kalshi_db) as conn:
        status = read_candle_status(conn, RULE_C)
    assert status is not None
    assert status.selected_open == 2  # POLITICS, NULLCAT: closed, not finalized
    assert status.complete_through_close == 1  # POLITICS
    assert status.closed_short_of_close == 1  # NULLCAT
    assert status.open_lagging == 1  # NULLCAT only
    assert status.open_oldest_watermark == closed_at - span
