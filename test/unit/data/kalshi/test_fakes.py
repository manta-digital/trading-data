"""Smoke tests for the sync-core test doubles (slice 262, Tasks 4.1/4.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from psycopg import errors

from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    CATALOG_WALK_FILTERS,
    KALSHI_MVE_FILTER,
    MarketStatus,
    MarketStatusFilter,
    Surface,
)
from manta_trading.data.kalshi.sync import CatalogSource
from manta_trading.providers.errors import ProviderTransientError

from ._fake_repository import FakeCatalogRepository
from ._fake_source import FakeCatalogSource, make_event, make_market, make_series

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
