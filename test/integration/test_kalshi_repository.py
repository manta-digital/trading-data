"""Integration tests: ``CatalogRepository`` on a throwaway database (slice 262).

Uses only ``ephemeral_db`` (``MT_TIMESCALE_TEST_URL``); never the production
URL. Rows are the recorded 261 fixtures parsed through the Pydantic models,
so every write uses a real served shape. The recorded pages do not chain
(``markets_page1`` events are not in ``events_page1``, whose series are not
in ``series_list``), so parent rows are synthesized from the child's own
parent ticker — the minimum that satisfies the foreign keys.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi.constants import MarketStatus, Surface
from manta_trading.data.kalshi.repository import CatalogRepository, MarketUpsertOutcome
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.runner import apply_migrations

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "test" / "fixtures" / "kalshi"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Row builders (recorded fixtures → models)
# ---------------------------------------------------------------------------


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def series_rows() -> list[km.Series]:
    return km.SeriesListResponse.model_validate(_load("series_list")).series


def event_rows(page: str = "events_page1") -> list[km.Event]:
    return km.EventsPage.model_validate(_load(page)).events


def market_rows(page: str = "markets_page1") -> list[km.Market]:
    return km.MarketsPage.model_validate(_load(page)).markets


def parent_series(events: Iterable[km.Event]) -> list[km.Series]:
    return [km.Series(ticker=t) for t in sorted({e.series_ticker for e in events})]


def parent_events(markets: Iterable[km.Market]) -> list[km.Event]:
    return [
        km.Event(event_ticker=t, series_ticker=f"{t}-SERIES")
        for t in sorted({m.event_ticker for m in markets})
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def kalshi_db(ephemeral_db: str) -> str:
    """Bare database → kalshi track applied."""
    with ConnectionPool[Any](ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, TRACKS["kalshi"])
    return ephemeral_db


@pytest.fixture
async def conn(kalshi_db: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    async with await psycopg.AsyncConnection.connect(kalshi_db) as connection:
        yield connection


@pytest.fixture
def repo(conn: psycopg.AsyncConnection[Any]) -> CatalogRepository:
    return CatalogRepository(conn)


async def write_catalog(
    repo: CatalogRepository, markets: list[km.Market]
) -> MarketUpsertOutcome:
    """Markets with synthesized parents, in one transaction."""
    events = parent_events(markets)
    async with repo.transaction():
        await repo.upsert_series(parent_series(events))
        await repo.upsert_events(events)
        outcome = await repo.upsert_markets(markets)
    return outcome


async def column(
    conn: psycopg.AsyncConnection[Any], query: str, *params: object
) -> list[Any]:
    cursor = await conn.execute(query, params)  # type: ignore[arg-type]
    return [row[0] for row in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Task 3.1 — scaffolding
# ---------------------------------------------------------------------------


class TestScaffolding:
    async def test_fixture_yields_empty_migrated_schema(
        self, conn: psycopg.AsyncConnection[Any]
    ):
        assert await column(conn, "SELECT count(*) FROM kalshi.markets") == [0]
        assert await column(conn, "SELECT count(*) FROM kalshi.sync_state") == [0]

    def test_row_builders_parse_real_shapes(self):
        assert len(series_rows()) > 0
        assert len(event_rows()) > 0
        assert {m.status for m in market_rows()} == {MarketStatus.FINALIZED.value}


# ---------------------------------------------------------------------------
# Task 3.2 — upsert_series
# ---------------------------------------------------------------------------


class TestUpsertSeries:
    async def test_write_on_change(
        self, repo: CatalogRepository, conn: psycopg.AsyncConnection[Any]
    ):
        rows = series_rows()
        async with repo.transaction():
            assert await repo.upsert_series(rows) == len(rows)
        first_seen = await column(
            conn, "SELECT first_seen_at FROM kalshi.series ORDER BY ticker"
        )
        synced = await column(
            conn, "SELECT last_synced_at FROM kalshi.series ORDER BY ticker"
        )

        async with repo.transaction():
            assert await repo.upsert_series(rows) == 0
        assert (
            await column(
                conn, "SELECT first_seen_at FROM kalshi.series ORDER BY ticker"
            )
            == first_seen
        )
        assert (
            await column(
                conn, "SELECT last_synced_at FROM kalshi.series ORDER BY ticker"
            )
            == synced
        )

        changed = rows[0].model_copy(update={"title": "changed title"})
        async with repo.transaction():
            assert await repo.upsert_series([changed, *rows[1:]]) == 1
        bumped = await column(
            conn,
            "SELECT ticker FROM kalshi.series WHERE last_synced_at > first_seen_at",
        )
        assert bumped == [changed.ticker]
        assert await column(
            conn, "SELECT title FROM kalshi.series WHERE ticker = %s", changed.ticker
        ) == ["changed title"]
        assert (
            await column(
                conn, "SELECT first_seen_at FROM kalshi.series ORDER BY ticker"
            )
            == first_seen
        )

    async def test_empty_input(self, repo: CatalogRepository):
        assert await repo.upsert_series([]) == 0


# ---------------------------------------------------------------------------
# Task 3.3 — upsert_events
# ---------------------------------------------------------------------------


class TestUpsertEvents:
    async def test_write_on_change(
        self, repo: CatalogRepository, conn: psycopg.AsyncConnection[Any]
    ):
        events = event_rows()
        async with repo.transaction():
            await repo.upsert_series(parent_series(events))
            assert await repo.upsert_events(events) == len(events)
            assert await repo.upsert_events(events) == 0
        changed = events[0].model_copy(update={"title": "changed"})
        async with repo.transaction():
            assert await repo.upsert_events([changed]) == 1
        bumped = await column(
            conn,
            "SELECT event_ticker FROM kalshi.events "
            "WHERE last_synced_at > first_seen_at",
        )
        assert bumped == [changed.event_ticker]

    async def test_missing_series_raises_fk_violation(self, repo: CatalogRepository):
        events = event_rows()
        with pytest.raises(errors.ForeignKeyViolation):
            async with repo.transaction():
                await repo.upsert_events(events)


# ---------------------------------------------------------------------------
# Task 3.4 — upsert_markets
# ---------------------------------------------------------------------------


class TestUpsertMarkets:
    async def test_new_page_then_transition_then_unchanged(
        self, repo: CatalogRepository
    ):
        markets = market_rows("markets_open")
        outcome = await write_catalog(repo, markets)
        assert outcome == MarketUpsertOutcome(written=len(markets), transitions={})

        closed = markets[0].model_copy(update={"status": MarketStatus.CLOSED.value})
        async with repo.transaction():
            outcome = await repo.upsert_markets([closed, *markets[1:]])
        assert outcome.written == 1
        assert outcome.transitions == {
            (MarketStatus.ACTIVE.value, MarketStatus.CLOSED.value): 1
        }

        async with repo.transaction():
            outcome = await repo.upsert_markets([closed, *markets[1:]])
        assert outcome == MarketUpsertOutcome(written=0, transitions={})

    async def test_unknown_status_raises_check_violation(self, repo: CatalogRepository):
        markets = market_rows("markets_open")
        bad = markets[0].model_copy(update={"status": "not-a-status"})
        with pytest.raises(errors.CheckViolation):
            await write_catalog(repo, [bad, *markets[1:]])

    async def test_raw_and_decimals_stored(
        self, repo: CatalogRepository, conn: psycopg.AsyncConnection[Any]
    ):
        markets = market_rows()
        await write_catalog(repo, markets)
        raw = await column(
            conn, "SELECT raw FROM kalshi.markets WHERE ticker = %s", markets[0].ticker
        )
        assert raw[0]["ticker"] == markets[0].ticker
        assert raw[0]["result"] == markets[0].result
        settled = await column(
            conn,
            "SELECT settlement_ts FROM kalshi.markets WHERE ticker = %s",
            markets[0].ticker,
        )
        assert settled == [markets[0].settlement_ts]


# ---------------------------------------------------------------------------
# Task 3.5 — parent lookups
# ---------------------------------------------------------------------------


class TestParentLookups:
    async def test_known_subsets(self, repo: CatalogRepository):
        events = event_rows()
        async with repo.transaction():
            await repo.upsert_series(parent_series(events))
            await repo.upsert_events(events)
        series = {e.series_ticker for e in events}
        assert await repo.known_series_tickers([*series, "NOPE"]) == series
        assert await repo.known_event_tickers([events[0].event_ticker, "NOPE"]) == {
            events[0].event_ticker
        }
        assert await repo.known_event_tickers([]) == set()
        assert await repo.known_series_tickers([]) == set()


# ---------------------------------------------------------------------------
# Task 3.6 — awaiting-settlement statements
# ---------------------------------------------------------------------------


class TestAwaiting:
    async def test_enter_retire_refresh_mark(
        self, repo: CatalogRepository, conn: psycopg.AsyncConnection[Any]
    ):
        markets = market_rows("markets_open")  # every one active, close in the future
        past = markets[0].model_copy(update={"close_time": NOW - timedelta(hours=1)})
        future = markets[1]
        await write_catalog(repo, [past, future])

        async with repo.transaction():
            assert await repo.enter_awaiting(NOW) == 1
            assert await repo.enter_awaiting(NOW) == 0
        assert await repo.awaiting_tickers() == [past.ticker]

        # mark_checked touches only the given tickers
        async with repo.transaction():
            assert await repo.mark_checked([past.ticker], NOW) == 1
            assert await repo.mark_checked([], NOW) == 0
        checked = await column(
            conn,
            "SELECT last_checked_at FROM kalshi.awaiting_settlement "
            "WHERE market_ticker = %s",
            past.ticker,
        )
        assert checked == [NOW]

        # review F005: a changed close_time propagates to the awaiting row
        moved = past.model_copy(update={"close_time": NOW - timedelta(hours=3)})
        async with repo.transaction():
            await repo.upsert_markets([moved])
            assert await repo.refresh_awaiting_close_times() == 1
            assert await repo.refresh_awaiting_close_times() == 0
        stored = await column(
            conn,
            "SELECT close_time FROM kalshi.awaiting_settlement "
            "WHERE market_ticker = %s",
            past.ticker,
        )
        assert stored == [moved.close_time]

        # retire only once finalized with a result
        finalized_no_result = moved.model_copy(
            update={"status": MarketStatus.FINALIZED.value, "result": None}
        )
        async with repo.transaction():
            await repo.upsert_markets([finalized_no_result])
            assert await repo.retire_awaiting() == 0
        finalized = moved.model_copy(
            update={"status": MarketStatus.FINALIZED.value, "result": "yes"}
        )
        async with repo.transaction():
            await repo.upsert_markets([finalized])
            assert await repo.retire_awaiting() == 1
        assert await repo.awaiting_tickers() == []


# ---------------------------------------------------------------------------
# Task 3.7 — sync_state
# ---------------------------------------------------------------------------


class TestSyncState:
    async def test_accessors(self, repo: CatalogRepository):
        assert await repo.get_sync_state(Surface.CATALOG) is None
        async with repo.transaction():
            await repo.set_watermark(Surface.CATALOG, NOW)
        state = await repo.get_sync_state(Surface.CATALOG)
        assert state is not None
        assert state.watermark_ts == NOW
        assert state.last_full_sync_at is None
        assert state.cursor is None

        async with repo.transaction():
            await repo.set_last_full_sync(Surface.CATALOG, NOW + timedelta(minutes=1))
        state = await repo.get_sync_state(Surface.CATALOG)
        assert state is not None
        assert state.watermark_ts == NOW
        assert state.last_full_sync_at == NOW + timedelta(minutes=1)
        assert state.cursor is None
        assert await repo.get_sync_state(Surface.TRADES) is None
