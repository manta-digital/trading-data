"""Smoke tests for the sync-core test doubles (slice 262, Tasks 4.1/4.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from kalshi_support.fake_candle_repository import FakeCandleRepository, FakeMarket
from kalshi_support.fake_candle_source import make_trade_candle
from kalshi_support.fake_historical_source import FakeHistoricalSource
from kalshi_support.fake_repository import FakeCatalogRepository
from kalshi_support.fake_source import (
    FakeCatalogSource,
    make_event,
    make_market,
    make_series,
)
from kalshi_support.fake_trade_repository import FakeTradeRepository
from kalshi_support.fake_trade_source import FakeTradeSource, make_trade
from psycopg import errors

from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    CATALOG_WALK_FILTERS,
    COLLECTED_CANDLE_PERIOD,
    KALSHI_MVE_FILTER,
    MarketStatus,
    MarketStatusFilter,
    Surface,
)
from manta_trading.data.kalshi.historical_types import HistoricalSource
from manta_trading.data.kalshi.models import Trade, TradesPage
from manta_trading.data.kalshi.sync import CatalogSource
from manta_trading.data.kalshi.sync_types import epoch
from manta_trading.data.kalshi.trade_repository import PageCounts, TradeState
from manta_trading.data.kalshi.trade_types import TradeSource
from manta_trading.providers.errors import ProviderTransientError

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _as_source(source: CatalogSource) -> CatalogSource:
    return source


class TestProtocol:
    def test_client_and_fake_satisfy_catalog_source(self):
        client = KalshiClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200))
        )
        assert _as_source(client) is client
        fake = FakeCatalogSource()
        assert _as_source(fake) is fake


class TestFakeSource:
    async def test_walk_each_filter_and_record_queries(self):
        fake = FakeCatalogSource()
        for status in CATALOG_WALK_FILTERS:
            rows = [
                m
                async for m in fake.iter_markets(
                    status=status, mve_filter=KALSHI_MVE_FILTER, limit=1000
                )
            ]
            expected = 5 if status is MarketStatusFilter.OPEN else 0
            assert len(rows) == expected
        assert [q["status"] for q in fake.markets_queries] == list(CATALOG_WALK_FILTERS)
        assert all(q["mve_filter"] == "exclude" for q in fake.markets_queries)

    async def test_pages_follow_cursor(self):
        fake = FakeCatalogSource(page_size=2)
        rows = [m async for m in fake.iter_markets(status=MarketStatusFilter.OPEN)]
        assert len(rows) == 5
        assert [q["cursor"] for q in fake.markets_queries] == [None, "2", "4"]

    async def test_settled_window_is_strict_and_newest_first(self):
        fake = FakeCatalogSource(load_fixtures=False)
        base = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
        rows = [
            make_market(f"T{i}", "EV", settlement_ts=base + timedelta(seconds=i))
            for i in range(5)
        ]
        fake.add_settled(*rows)
        lo = int(base.timestamp()) + 1
        hi = int(base.timestamp()) + 4
        page = await fake.get_markets(min_settled_ts=lo, max_settled_ts=hi)
        assert [m.ticker for m in page.markets] == ["T3", "T2"]

    async def test_tickers_lookup_omits_unknown(self):
        fake = FakeCatalogSource(load_fixtures=False)
        fake.add_lookup(make_market("A", "EV"))
        page = await fake.get_markets(tickers="A,B", mve_filter=KALSHI_MVE_FILTER)
        assert [m.ticker for m in page.markets] == ["A"]
        events = await fake.get_events(tickers="KXELONMARS-99,NOPE")
        assert events.events == []

    async def test_events_by_min_updated_ts(self):
        fake = FakeCatalogSource(load_fixtures=False)
        fake.add_events(
            make_event("E1", "S", last_updated_ts=NOW),
            make_event("E2", "S", last_updated_ts=NOW + timedelta(seconds=5)),
        )
        page = await fake.get_events(min_updated_ts=int(NOW.timestamp()))
        assert [e.event_ticker for e in page.events] == ["E2"]

    async def test_raise_on_by_count_and_predicate(self):
        fake = FakeCatalogSource()
        fake.raise_on("get_markets", ProviderTransientError("boom"), at=2)
        await fake.get_markets(status=MarketStatusFilter.OPEN)
        with pytest.raises(ProviderTransientError):
            await fake.get_markets(status=MarketStatusFilter.OPEN)
        fake2 = FakeCatalogSource()
        fake2.raise_on(
            "get_markets",
            ProviderTransientError("boom"),
            when=lambda q: q.get("status") is MarketStatusFilter.CLOSED,
        )
        await fake2.get_markets(status=MarketStatusFilter.OPEN)
        with pytest.raises(ProviderTransientError):
            await fake2.get_markets(status=MarketStatusFilter.CLOSED)


class TestFakeRepository:
    async def test_upsert_page_and_read_back(self):
        repo = FakeCatalogRepository(now=NOW)
        market = make_market("M1", "E1", status=MarketStatus.ACTIVE.value)
        other = make_market("M2", "E1", status=MarketStatus.ACTIVE.value)
        async with repo.transaction():
            assert await repo.upsert_series([make_series("S1")]) == 1
            assert await repo.upsert_events([make_event("E1", "S1")]) == 1
            outcome = await repo.upsert_markets([market, other])
        assert outcome.written == 2 and outcome.transitions == {}
        assert repo.markets["M1"].status == "active"
        assert repo.tx_log == ["begin", "commit"]
        assert await repo.known_event_tickers(["E1", "E2"]) == {"E1"}

        closed = market.model_copy(update={"status": MarketStatus.CLOSED.value})
        async with repo.transaction():
            outcome = await repo.upsert_markets([closed, other])
        assert outcome.written == 1
        assert outcome.transitions == {("active", "closed"): 1}

    async def test_integrity_errors_and_rollback(self):
        repo = FakeCatalogRepository(now=NOW)
        with pytest.raises(errors.ForeignKeyViolation):
            async with repo.transaction():
                await repo.upsert_events([make_event("E1", "S-missing")])
        assert repo.tx_log == ["begin", "rollback"]
        assert repo.events == {}

        async with repo.transaction():
            await repo.upsert_series([make_series("S1")])
            await repo.upsert_events([make_event("E1", "S1")])
        with pytest.raises(errors.CheckViolation):
            async with repo.transaction():
                await repo.upsert_markets(
                    [make_market("OK", "E1"), make_market("BAD", "E1", status="bogus")]
                )
        assert repo.markets == {}, "the whole page rolled back"

    async def test_fail_on_injects_any_exception(self):
        repo = FakeCatalogRepository(now=NOW)
        repo.fail_on("set_watermark", errors.OperationalError("gone"))
        with pytest.raises(errors.OperationalError):
            await repo.set_watermark(Surface.CATALOG, NOW)

    async def test_awaiting_and_sync_state(self):
        repo = FakeCatalogRepository(now=NOW)
        async with repo.transaction():
            await repo.upsert_series([make_series("S1")])
            await repo.upsert_events([make_event("E1", "S1")])
            await repo.upsert_markets(
                [
                    make_market(
                        "PAST",
                        "E1",
                        status="active",
                        close_time=NOW - timedelta(hours=1),
                    ),
                    make_market(
                        "FUTURE",
                        "E1",
                        status="active",
                        close_time=NOW + timedelta(hours=1),
                    ),
                ]
            )
            assert await repo.enter_awaiting(NOW) == 1
            assert await repo.awaiting_tickers() == ["PAST"]
            assert await repo.mark_checked(["PAST"], NOW) == 1
            await repo.upsert_markets(
                [
                    make_market(
                        "PAST",
                        "E1",
                        status="finalized",
                        result="yes",
                        close_time=NOW - timedelta(hours=1),
                    )
                ]
            )
            assert await repo.retire_awaiting() == 1
        assert repo.awaiting == {}
        assert await repo.get_sync_state(Surface.CATALOG) is None
        await repo.set_watermark(Surface.CATALOG, NOW)
        state = await repo.get_sync_state(Surface.CATALOG)
        assert (
            state is not None
            and state.watermark_ts == NOW
            and state.last_full_sync_at is None
        )


# ---------------------------------------------------------------------------
# Trades fakes (slice 265, Task 4.3a)
# ---------------------------------------------------------------------------


def _as_trade_source(source: TradeSource) -> TradeSource:
    return source


class TestTradeProtocol:
    def test_client_and_fake_satisfy_trade_source(self):
        """Pinned at runtime and in the type gate: the real client's
        ``**query: Unpack[TradesQuery]`` must accept the protocol's keyword
        calls, and the mypy ``Unpack`` path artifact makes the type gate the
        least reliable place to learn of a mismatch."""
        client = KalshiClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200))
        )
        assert _as_trade_source(client) is client
        fake = FakeTradeSource()
        assert _as_trade_source(fake) is fake


class TestFakeTradeSource:
    def _tape(self, source: FakeTradeSource, count: int) -> list[Trade]:
        rows = [make_trade("M1", NOW + timedelta(seconds=i)) for i in range(count)]
        source.add_trades(*rows)
        return rows

    async def test_window_is_strict_after_inclusive_through_newest_first(self):
        source = FakeTradeSource()
        rows = self._tape(source, 5)
        page = await source.get_trades(
            min_ts=epoch(rows[1].created_time),
            max_ts=epoch(rows[3].created_time),
            limit=100,
        )
        assert [t.trade_id for t in page.trades] == [rows[3].trade_id, rows[2].trade_id]
        assert page.cursor == ""
        assert source.trade_queries[0]["limit"] == 100

    async def test_pages_follow_cursor_and_the_last_is_empty(self):
        source = FakeTradeSource(page_size=2)
        rows = self._tape(source, 5)
        lo, hi = epoch(NOW) - 1, epoch(NOW) + 10
        pages: list[TradesPage] = []
        cursor: str | None = None
        while True:
            page = await source.get_trades(
                cursor=cursor, min_ts=lo, max_ts=hi, limit=100
            )
            pages.append(page)
            if not page.cursor:
                break
            cursor = page.cursor
        assert [len(p.trades) for p in pages] == [2, 2, 1]
        assert [q["cursor"] for q in source.trade_queries] == [None, "2", "4"]
        assert {t.trade_id for p in pages for t in p.trades} == {
            r.trade_id for r in rows
        }

    async def test_cutoff_and_raise_on(self):
        source = FakeTradeSource()
        source.set_cutoff(NOW)
        assert (await source.get_historical_cutoff()).trades_created_ts == NOW
        source.raise_on("get_trades", ProviderTransientError("503"), at=2)
        await source.get_trades(min_ts=0, max_ts=1, limit=1)
        with pytest.raises(ProviderTransientError):
            await source.get_trades(min_ts=0, max_ts=1, limit=1)


class TestFakeTradeRepository:
    async def test_write_page_classifies_by_declared_sets_and_conflict_ignores(self):
        repo = FakeTradeRepository()
        repo.unknown_tickers.add("KXMVE-1")
        repo.excluded_tickers.add("SPORTS")
        page = [
            make_trade("KXMVE-1", NOW),
            make_trade("SPORTS", NOW),
            make_trade("POL", NOW),
        ]
        async with repo.transaction():
            first = await repo.write_page(page)
            second = await repo.write_page(page)
        assert first == PageCounts(3, 1, 1, 1, 1, unknown_tickers=("KXMVE-1",))
        assert second == PageCounts(3, 1, 1, 1, 0, unknown_tickers=("KXMVE-1",))
        assert {ticker for ticker, _, _ in repo.stored} == {"POL"}
        assert repo.tx_log == ["begin", "commit"]

    async def test_state_row_and_rollback(self):
        repo = FakeTradeRepository()
        assert await repo.read_state() is None
        assert await repo.read_catalog_walk_start() is None
        async with repo.transaction():
            await repo.init_state(NOW, NOW)
            await repo.init_state(NOW + timedelta(days=1), NOW + timedelta(days=1))
        assert repo.state == TradeState(NOW, NOW)
        repo.catalog_walk_start = NOW + timedelta(hours=2)
        assert await repo.read_catalog_walk_start() == NOW + timedelta(hours=2)
        with pytest.raises(errors.OperationalError):
            async with repo.transaction():
                await repo.advance_watermark(NOW + timedelta(hours=1))
                raise errors.OperationalError("lost")
        assert repo.state == TradeState(NOW, NOW)
        assert repo.tx_log[-1] == "rollback"

    async def test_fail_on_injects_any_exception(self):
        repo = FakeTradeRepository()
        repo.fail_on("write_page", errors.OperationalError("lost"), at=2)
        await repo.write_page([])
        with pytest.raises(errors.OperationalError):
            await repo.write_page([])


class TestHistoricalFakes:
    """Slice 267, Task 6.3: the historical fakes' own contracts."""

    def test_fake_satisfies_historical_source(self):
        def as_source(source: HistoricalSource) -> HistoricalSource:
            return source

        assert as_source(FakeHistoricalSource()) is not None

    async def test_archive_pages_are_served_behind_opaque_cursors(self):
        source = FakeHistoricalSource()
        source.add_archive_page(make_market("A", "E"), make_market("B", "E"))
        source.add_archive_page(make_market("C", "E"))
        first = await source.get_historical_markets(limit=2, mve_filter="exclude")
        assert [m.ticker for m in first.markets] == ["A", "B"] and first.cursor
        last = await source.get_historical_markets(cursor=first.cursor, limit=2)
        assert [m.ticker for m in last.markets] == ["C"] and last.cursor == ""
        beyond = await source.get_historical_markets(cursor="archive-9")
        assert beyond.markets == [] and beyond.cursor == ""
        assert [q["cursor"] for q in source.archive_queries] == [
            None,
            first.cursor,
            "archive-9",
        ]

    async def test_candles_are_filtered_to_the_range_and_trades_delegate(self):
        source = FakeHistoricalSource(page_size=1)
        source.add_candles(
            "T", make_trade_candle(NOW), make_trade_candle(NOW + timedelta(minutes=1))
        )
        served = await source.get_historical_market_candlesticks(
            "T",
            start_ts=epoch(NOW) - 60,
            end_ts=epoch(NOW),
            period_interval=COLLECTED_CANDLE_PERIOD,
        )
        assert [c.end_period_ts for c in served] == [NOW]
        source.add_trades(make_trade("POL", NOW), make_trade("POL", NOW - timedelta(1)))
        page = await source.get_historical_trades(
            min_ts=epoch(NOW) - 60, max_ts=epoch(NOW), limit=10
        )
        assert len(page.trades) == 1 and page.cursor == ""
        assert source.trade_queries[0]["limit"] == 10
        source.raise_on("get_historical_trades", ProviderTransientError("503"))
        with pytest.raises(ProviderTransientError):
            await source.get_historical_trades(min_ts=1, max_ts=2, limit=1)

    async def test_candle_repository_pending_behind_cutoff(self):
        repo = FakeCandleRepository()
        cutoff = NOW
        for i, ticker in enumerate(["OLDER", "OLD", "AFTER"]):
            settled = cutoff + timedelta(days=i - 2)  # -2d, -1d, +0d
            repo.add_market(
                FakeMarket(
                    ticker,
                    settled - timedelta(hours=2),
                    settled - timedelta(minutes=1),
                    status=MarketStatus.FINALIZED.value,
                    settlement_ts=settled,
                )
            )
        period = COLLECTED_CANDLE_PERIOD
        assert [
            r.ticker for r in await repo.pending_behind_cutoff(period, cutoff, None)
        ] == [
            "OLDER",
            "OLD",
        ]
        assert [
            r.ticker for r in await repo.pending_behind_cutoff(period, cutoff, 1)
        ] == ["OLDER"]
        assert repo.pending_limits == [None, 1]
        assert await repo.count_behind_cutoff(period, cutoff) == 2

    async def test_trade_repository_surface_cursor_and_live_floor(self):
        repo = FakeTradeRepository(surface=Surface.HISTORICAL)
        assert repo.surface is Surface.HISTORICAL
        assert await repo.read_live_coverage_from() is None
        repo.live_coverage_from = NOW
        assert await repo.read_live_coverage_from() == NOW
        await repo.set_cursor("archive-1")
        assert await repo.read_cursor() == "archive-1"
        await repo.set_cursor(None)
        assert repo.cursor_log == ["archive-1", None]
        # Set once: a NULL instant is filled, a set one is kept.
        repo.state = TradeState(None, None)
        await repo.init_state(NOW, NOW - timedelta(days=1))
        await repo.init_state(NOW + timedelta(days=9), NOW)
        assert repo.state == TradeState(NOW, NOW - timedelta(days=1))
