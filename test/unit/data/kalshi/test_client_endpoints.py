"""Endpoint-method tests for ``KalshiClient`` (slice 261, Section 5).

Task 5.0 harness: a ``KalshiClient`` over ``httpx.MockTransport`` routed by
path from the Section 3 inline samples, recording every outgoing request.
Two-page cursor routes serve page 1 without a ``cursor`` param and page 2
when ``cursor`` equals page 1's cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import pytest
from kalshi_support.samples import (
    CANDLE_SAMPLE,
    CUTOFF_SAMPLE,
    EVENT_SAMPLE,
    MARKET_SAMPLE,
    SERIES_SAMPLE,
    TRADE_SAMPLE,
)

from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    CandlePeriod,
    EventStatusFilter,
    MarketStatusFilter,
)
from manta_trading.data.kalshi.models import (
    Candlestick,
    Event,
    HistoricalCutoff,
    Market,
    Series,
    Trade,
)
from manta_trading.providers.errors import ProviderPermanentError

PAGE2_CURSOR = "cursor-page-2"

#: A route serves one payload, or a two-element list for cursor-paged routes.
Route = dict[str, Any] | list[dict[str, Any]]


def paged(key: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two pages: the first with a cursor, the second without."""
    return [{key: items[:1], "cursor": PAGE2_CURSOR}, {key: items[1:]}]


ROUTES: dict[str, Route] = {
    "/series": {"series": [SERIES_SAMPLE]},
    "/series/FED": {"series": SERIES_SAMPLE},
    "/events": paged("events", [EVENT_SAMPLE, {**EVENT_SAMPLE, "event_ticker": "E2"}]),
    "/events/KXELONMARS-99": {"event": EVENT_SAMPLE, "markets": [MARKET_SAMPLE]},
    "/markets": paged("markets", [MARKET_SAMPLE, {**MARKET_SAMPLE, "ticker": "M2"}]),
    "/markets/M1": {"market": MARKET_SAMPLE},
    "/series/KXELONMARS/markets/KXELONMARS-99/candlesticks": {
        "ticker": "KXELONMARS-99",
        "candlesticks": [CANDLE_SAMPLE],
    },
    "/markets/trades": paged(
        "trades", [TRADE_SAMPLE, {**TRADE_SAMPLE, "trade_id": "t2"}]
    ),
    "/historical/cutoff": CUTOFF_SAMPLE,
}


@dataclass
class Harness:
    client: KalshiClient
    requests: list[httpx.Request]

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def query(self, index: int = -1) -> dict[str, str]:
        return dict(self.requests[index].url.params)


def build_harness(routes: dict[str, Route] = ROUTES) -> Harness:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        route = routes.get(request.url.path.removeprefix("/trade-api/v2"))
        if route is None:
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        if isinstance(route, list):
            cursor = request.url.params.get("cursor")
            return httpx.Response(200, json=route[1 if cursor == PAGE2_CURSOR else 0])
        return httpx.Response(200, json=route)

    client = KalshiClient(transport=httpx.MockTransport(handler))
    return Harness(client=client, requests=requests)


@pytest.fixture
def harness() -> Harness:
    return build_harness()


class TestHarness:
    async def test_smoke_route(self, harness: Harness):
        cutoff = await harness.client.get_historical_cutoff()
        assert isinstance(cutoff, HistoricalCutoff)
        assert harness.last.url.path == "/trade-api/v2/historical/cutoff"

    async def test_unrouted_path_is_permanent_error(self, harness: Harness):
        with pytest.raises(ProviderPermanentError):
            await harness.client.get_market("UNKNOWN")


class TestSeries:
    async def test_list_path_and_filters(self, harness: Harness):
        series = await harness.client.get_series_list(
            category="Economics", min_updated_ts=1700000000, include_volume=True
        )
        assert harness.last.url.path == "/trade-api/v2/series"
        assert harness.query() == {
            "category": "Economics",
            "min_updated_ts": "1700000000",
            "include_volume": "true",
        }
        assert isinstance(series[0], Series)
        assert series[0].ticker == "FED"

    async def test_list_omits_unset_filters(self, harness: Harness):
        await harness.client.get_series_list()
        assert harness.query() == {}

    async def test_single(self, harness: Harness):
        series = await harness.client.get_series("FED")
        assert harness.last.url.path == "/trade-api/v2/series/FED"
        assert series.fee_multiplier == Decimal("1")


class TestEvents:
    async def test_page_query(self, harness: Harness):
        page = await harness.client.get_events(
            status=EventStatusFilter.OPEN,
            series_ticker="KXELONMARS",
            with_nested_markets=False,
            limit=50,
        )
        assert harness.last.url.path == "/trade-api/v2/events"
        assert harness.query() == {
            "status": "open",
            "series_ticker": "KXELONMARS",
            "with_nested_markets": "false",
            "limit": "50",
        }
        assert page.cursor == PAGE2_CURSOR
        assert isinstance(page.events[0], Event)

    async def test_iter_follows_cursor_and_terminates(self, harness: Harness):
        events = [e async for e in harness.client.iter_events(limit=1)]
        assert [e.event_ticker for e in events] == ["KXELONMARS-99", "E2"]
        assert len(harness.requests) == 2
        assert "cursor" not in harness.query(0)
        assert harness.query(1)["cursor"] == PAGE2_CURSOR
        assert harness.query(1)["limit"] == "1"

    async def test_single_folds_top_level_markets(self, harness: Harness):
        event = await harness.client.get_event(
            "KXELONMARS-99", with_nested_markets=True
        )
        assert harness.last.url.path == "/trade-api/v2/events/KXELONMARS-99"
        assert harness.query() == {"with_nested_markets": "true"}
        assert event.markets is not None
        assert event.markets[0].ticker == MARKET_SAMPLE["ticker"]


class TestMarkets:
    async def test_page_query_with_timestamp_range(self, harness: Harness):
        page = await harness.client.get_markets(
            status=MarketStatusFilter.SETTLED,
            min_settled_ts=1755000000,
            max_settled_ts=1756000000,
            mve_filter="exclude",
            limit=200,
        )
        assert harness.last.url.path == "/trade-api/v2/markets"
        assert harness.query() == {
            "status": "settled",
            "min_settled_ts": "1755000000",
            "max_settled_ts": "1756000000",
            "mve_filter": "exclude",
            "limit": "200",
        }
        assert isinstance(page.markets[0], Market)
        assert page.markets[0].status == "finalized"

    async def test_iter_follows_cursor(self, harness: Harness):
        markets = [m async for m in harness.client.iter_markets(min_updated_ts=1)]
        assert [m.ticker for m in markets] == [MARKET_SAMPLE["ticker"], "M2"]
        assert harness.query(1) == {"min_updated_ts": "1", "cursor": PAGE2_CURSOR}

    async def test_single(self, harness: Harness):
        market = await harness.client.get_market("M1")
        assert harness.last.url.path == "/trade-api/v2/markets/M1"
        assert market.notional_value_dollars == Decimal("1.0000")


class TestCandlesticks:
    async def test_path_and_required_params(self, harness: Harness):
        candles = await harness.client.get_market_candlesticks(
            "KXELONMARS",
            "KXELONMARS-99",
            start_ts=1787400000,
            end_ts=1787600000,
            period_interval=CandlePeriod.HOUR,
        )
        assert (
            harness.last.url.path
            == "/trade-api/v2/series/KXELONMARS/markets/KXELONMARS-99/candlesticks"
        )
        assert harness.query() == {
            "start_ts": "1787400000",
            "end_ts": "1787600000",
            "period_interval": "60",
            "include_latest_before_start": "false",
        }
        assert isinstance(candles[0], Candlestick)
        assert candles[0].yes_bid.close_dollars == Decimal("0.1000")


class TestTrades:
    async def test_page_query(self, harness: Harness):
        page = await harness.client.get_trades(
            ticker="KXBTC15M-26AUG241115-15", min_ts=1, max_ts=2, is_block_trade=False
        )
        assert harness.last.url.path == "/trade-api/v2/markets/trades"
        assert harness.query() == {
            "ticker": "KXBTC15M-26AUG241115-15",
            "min_ts": "1",
            "max_ts": "2",
            "is_block_trade": "false",
        }
        assert isinstance(page.trades[0], Trade)

    async def test_iter_follows_cursor(self, harness: Harness):
        trades = [t async for t in harness.client.iter_trades(limit=1)]
        assert [t.trade_id for t in trades] == [TRADE_SAMPLE["trade_id"], "t2"]
        assert harness.query(1)["cursor"] == PAGE2_CURSOR


class TestHistoricalCutoff:
    async def test_path_and_model(self, harness: Harness):
        cutoff = await harness.client.get_historical_cutoff()
        assert harness.last.url.path == "/trade-api/v2/historical/cutoff"
        assert harness.query() == {}
        assert cutoff.market_settled_ts.year == 2026
