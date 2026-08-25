"""Integration tests: ``CatalogRepository`` on a throwaway database (slice 262).

Uses only ``ephemeral_db`` (``MT_TIMESCALE_TEST_URL``); never the production
URL. Fixtures ``kalshi_db`` / ``kalshi_conn`` / ``kalshi_repo`` live in the
tier's ``conftest.py``; row builders in ``kalshi_helpers``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from kalshi_helpers import (
    column,
    event_rows,
    market_rows,
    parent_series,
    series_rows,
    write_catalog,
)
from psycopg import errors

from manta_trading.data.kalshi.constants import MarketStatus, Surface
from manta_trading.data.kalshi.repository import CatalogRepository, MarketUpsertOutcome

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Task 3.1 — scaffolding
# ---------------------------------------------------------------------------


class TestScaffolding:
    async def test_fixture_yields_empty_migrated_schema(
        self, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        assert await column(kalshi_conn, "SELECT count(*) FROM kalshi.markets") == [0]
        assert await column(kalshi_conn, "SELECT count(*) FROM kalshi.sync_state") == [
            0
        ]

    def test_row_builders_parse_real_shapes(self):
        assert len(series_rows()) > 0
        assert len(event_rows()) > 0
        assert {m.status for m in market_rows()} == {MarketStatus.FINALIZED.value}


# ---------------------------------------------------------------------------
# Task 3.2 — upsert_series
# ---------------------------------------------------------------------------


class TestUpsertSeries:
    async def test_write_on_change(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        rows = series_rows()
        async with kalshi_repo.transaction():
            assert await kalshi_repo.upsert_series(rows) == len(rows)
        first_seen = await column(
            kalshi_conn, "SELECT first_seen_at FROM kalshi.series ORDER BY ticker"
        )
        synced = await column(
            kalshi_conn, "SELECT last_synced_at FROM kalshi.series ORDER BY ticker"
        )

        async with kalshi_repo.transaction():
            assert await kalshi_repo.upsert_series(rows) == 0
        assert (
            await column(
                kalshi_conn, "SELECT first_seen_at FROM kalshi.series ORDER BY ticker"
            )
            == first_seen
        )
        assert (
            await column(
                kalshi_conn, "SELECT last_synced_at FROM kalshi.series ORDER BY ticker"
            )
            == synced
        )

        changed = rows[0].model_copy(update={"title": "changed title"})
        async with kalshi_repo.transaction():
            assert await kalshi_repo.upsert_series([changed, *rows[1:]]) == 1
        bumped = await column(
            kalshi_conn,
            "SELECT ticker FROM kalshi.series WHERE last_synced_at > first_seen_at",
        )
        assert bumped == [changed.ticker]
        assert await column(
            kalshi_conn,
            "SELECT title FROM kalshi.series WHERE ticker = %s",
            changed.ticker,
        ) == ["changed title"]
        assert (
            await column(
                kalshi_conn, "SELECT first_seen_at FROM kalshi.series ORDER BY ticker"
            )
            == first_seen
        )

    async def test_empty_input(self, kalshi_repo: CatalogRepository):
        assert await kalshi_repo.upsert_series([]) == 0


# ---------------------------------------------------------------------------
# Task 3.3 — upsert_events
# ---------------------------------------------------------------------------


class TestUpsertEvents:
    async def test_write_on_change(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        events = event_rows()
        async with kalshi_repo.transaction():
            await kalshi_repo.upsert_series(parent_series(events))
            assert await kalshi_repo.upsert_events(events) == len(events)
            assert await kalshi_repo.upsert_events(events) == 0
        changed = events[0].model_copy(update={"title": "changed"})
        async with kalshi_repo.transaction():
            assert await kalshi_repo.upsert_events([changed]) == 1
        bumped = await column(
            kalshi_conn,
            "SELECT event_ticker FROM kalshi.events "
            "WHERE last_synced_at > first_seen_at",
        )
        assert bumped == [changed.event_ticker]

    async def test_missing_series_raises_fk_violation(
        self, kalshi_repo: CatalogRepository
    ):
        events = event_rows()
        with pytest.raises(errors.ForeignKeyViolation):
            async with kalshi_repo.transaction():
                await kalshi_repo.upsert_events(events)


# ---------------------------------------------------------------------------
# Task 3.4 — upsert_markets
# ---------------------------------------------------------------------------


class TestUpsertMarkets:
    async def test_new_page_then_transition_then_unchanged(
        self, kalshi_repo: CatalogRepository
    ):
        markets = market_rows("markets_open")
        outcome = await write_catalog(kalshi_repo, markets)
        assert outcome == MarketUpsertOutcome(written=len(markets), transitions={})

        closed = markets[0].model_copy(update={"status": MarketStatus.CLOSED.value})
        async with kalshi_repo.transaction():
            outcome = await kalshi_repo.upsert_markets([closed, *markets[1:]])
        assert outcome.written == 1
        assert outcome.transitions == {
            (MarketStatus.ACTIVE.value, MarketStatus.CLOSED.value): 1
        }

        async with kalshi_repo.transaction():
            outcome = await kalshi_repo.upsert_markets([closed, *markets[1:]])
        assert outcome == MarketUpsertOutcome(written=0, transitions={})

    async def test_unknown_status_raises_check_violation(
        self, kalshi_repo: CatalogRepository
    ):
        markets = market_rows("markets_open")
        bad = markets[0].model_copy(update={"status": "not-a-status"})
        with pytest.raises(errors.CheckViolation):
            await write_catalog(kalshi_repo, [bad, *markets[1:]])

    async def test_raw_and_decimals_stored(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        markets = market_rows()
        await write_catalog(kalshi_repo, markets)
        raw = await column(
            kalshi_conn,
            "SELECT raw FROM kalshi.markets WHERE ticker = %s",
            markets[0].ticker,
        )
        assert raw[0]["ticker"] == markets[0].ticker
        assert raw[0]["result"] == markets[0].result
        settled = await column(
            kalshi_conn,
            "SELECT settlement_ts FROM kalshi.markets WHERE ticker = %s",
            markets[0].ticker,
        )
        assert settled == [markets[0].settlement_ts]


# ---------------------------------------------------------------------------
# Task 3.5 — parent lookups
# ---------------------------------------------------------------------------


class TestParentLookups:
    async def test_known_subsets(self, kalshi_repo: CatalogRepository):
        events = event_rows()
        async with kalshi_repo.transaction():
            await kalshi_repo.upsert_series(parent_series(events))
            await kalshi_repo.upsert_events(events)
        series = {e.series_ticker for e in events}
        assert await kalshi_repo.known_series_tickers([*series, "NOPE"]) == series
        assert await kalshi_repo.known_event_tickers(
            [events[0].event_ticker, "NOPE"]
        ) == {events[0].event_ticker}
        assert await kalshi_repo.known_event_tickers([]) == set()
        assert await kalshi_repo.known_series_tickers([]) == set()


# ---------------------------------------------------------------------------
# Task 3.6 — awaiting-settlement statements
# ---------------------------------------------------------------------------


class TestAwaiting:
    async def test_enter_retire_refresh_mark(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        markets = market_rows("markets_open")  # every one active, close in the future
        past = markets[0].model_copy(update={"close_time": NOW - timedelta(hours=1)})
        future = markets[1]
        await write_catalog(kalshi_repo, [past, future])

        async with kalshi_repo.transaction():
            assert await kalshi_repo.enter_awaiting(NOW) == 1
            assert await kalshi_repo.enter_awaiting(NOW) == 0
        assert await kalshi_repo.awaiting_tickers() == [past.ticker]

        # mark_checked touches only the given tickers
        async with kalshi_repo.transaction():
            assert await kalshi_repo.mark_checked([past.ticker], NOW) == 1
            assert await kalshi_repo.mark_checked([], NOW) == 0
        checked = await column(
            kalshi_conn,
            "SELECT last_checked_at FROM kalshi.awaiting_settlement "
            "WHERE market_ticker = %s",
            past.ticker,
        )
        assert checked == [NOW]

        # review F005: a changed close_time propagates to the awaiting row
        moved = past.model_copy(update={"close_time": NOW - timedelta(hours=3)})
        async with kalshi_repo.transaction():
            await kalshi_repo.upsert_markets([moved])
            assert await kalshi_repo.refresh_awaiting_close_times() == 1
            assert await kalshi_repo.refresh_awaiting_close_times() == 0
        stored = await column(
            kalshi_conn,
            "SELECT close_time FROM kalshi.awaiting_settlement "
            "WHERE market_ticker = %s",
            past.ticker,
        )
        assert stored == [moved.close_time]

        # retire only once finalized with a result
        finalized_no_result = moved.model_copy(
            update={"status": MarketStatus.FINALIZED.value, "result": None}
        )
        async with kalshi_repo.transaction():
            await kalshi_repo.upsert_markets([finalized_no_result])
            assert await kalshi_repo.retire_awaiting() == 0
        finalized = moved.model_copy(
            update={"status": MarketStatus.FINALIZED.value, "result": "yes"}
        )
        async with kalshi_repo.transaction():
            await kalshi_repo.upsert_markets([finalized])
            assert await kalshi_repo.retire_awaiting() == 1
        assert await kalshi_repo.awaiting_tickers() == []


# ---------------------------------------------------------------------------
# Task 3.7 — sync_state
# ---------------------------------------------------------------------------


class TestSyncState:
    async def test_accessors(self, kalshi_repo: CatalogRepository):
        assert await kalshi_repo.get_sync_state(Surface.CATALOG) is None
        async with kalshi_repo.transaction():
            await kalshi_repo.set_watermark(Surface.CATALOG, NOW)
        state = await kalshi_repo.get_sync_state(Surface.CATALOG)
        assert state is not None
        assert state.watermark_ts == NOW
        assert state.last_full_sync_at is None
        assert state.cursor is None

        async with kalshi_repo.transaction():
            await kalshi_repo.set_last_full_sync(
                Surface.CATALOG, NOW + timedelta(minutes=1)
            )
        state = await kalshi_repo.get_sync_state(Surface.CATALOG)
        assert state is not None
        assert state.watermark_ts == NOW
        assert state.last_full_sync_at == NOW + timedelta(minutes=1)
        assert state.cursor is None
        assert await kalshi_repo.get_sync_state(Surface.TRADES) is None
