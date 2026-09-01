"""Endpoint-method tests for ``KalshiClient`` (slice 261, Section 5).

Task 5.0 harness: a ``KalshiClient`` over ``httpx.MockTransport`` routed by
path from the Section 3 inline samples, recording every outgoing request.
Two-page cursor routes serve page 1 without a ``cursor`` param and page 2
when ``cursor`` equals page 1's cursor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
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
    HISTORICAL_MARKET_CANDLESTICKS_PATH,
    HISTORICAL_MARKETS_PATH,
    HISTORICAL_TRADES_PATH,
    KALSHI_MVE_FILTER,
    MARKETS_PAGE_LIMIT,
    CandlePeriod,
    EventStatusFilter,
    MarketStatusFilter,
)
from manta_trading.data.kalshi.models import (
    Candlestick,
    Event,
    HistoricalCutoff,
    Market,
    MarketsPage,
    Series,
    Trade,
    TradesPage,
)
from manta_trading.providers.errors import ProviderPermanentError

PAGE2_CURSOR = "cursor-page-2"
FIXTURE_DIR = Path(__file__).resolve().parents[4] / "test" / "fixtures" / "kalshi"

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
    "/markets/candlesticks": {
        "markets": [
            {"market_ticker": "KXELONMARS-99", "candlesticks": [CANDLE_SAMPLE]},
            {"market_ticker": "IDLE-1", "candlesticks": []},
        ]
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


class TestBatchCandlesticks:
    """Slice 264: ``GET /markets/candlesticks`` — one request per batch."""

    async def test_path_params_and_omission(self, harness: Harness):
        markets = await harness.client.get_markets_candlesticks(
            ["KXELONMARS-99", "IDLE-1", "NOPE-NOT-A-TICKER"],
            start_ts=1787400000,
            end_ts=1787403600,
            period_interval=CandlePeriod.MINUTE,
        )
        assert len(harness.requests) == 1
        assert harness.last.url.path == "/trade-api/v2/markets/candlesticks"
        assert harness.query() == {
            "market_tickers": "KXELONMARS-99,IDLE-1,NOPE-NOT-A-TICKER",
            "start_ts": "1787400000",
            "end_ts": "1787403600",
            "period_interval": "1",
        }
        # The unknown ticker is absent; the idle one is present and empty.
        assert [m.market_ticker for m in markets] == ["KXELONMARS-99", "IDLE-1"]
        assert isinstance(markets[0].candlesticks[0], Candlestick)
        assert markets[1].candlesticks == []


class TestBatchCandlesticksFixture:
    """The recorded batch response (slice 264, Task 1.5) parses as served."""

    async def test_fixture_parses_including_empty_and_quote_only(self):
        wire = json.loads((FIXTURE_DIR / "candlesticks_batch.json").read_bytes())
        harness = build_harness({"/markets/candlesticks": wire})
        markets = await harness.client.get_markets_candlesticks(
            ["ignored-by-the-mock"],
            start_ts=0,
            end_ts=1,
            period_interval=CandlePeriod.MINUTE,
        )
        assert [m.market_ticker for m in markets] == [
            e["market_ticker"] for e in wire["markets"]
        ]
        # An idle market is present with an empty list, not absent.
        empty = [m for m in markets if not m.candlesticks]
        assert empty, "fixture must contain an empty entry"
        # A never-traded market's candle serves ``price: {}`` — every price
        # field None, volume zero — and still parses as a Candlestick.
        quote_only = [
            c
            for m in markets
            for c in m.candlesticks
            if c.price.model_dump(exclude_none=True) == {}
        ]
        assert quote_only, "fixture must contain a price: {} candle"
        assert all(c.volume_fp == 0 for c in quote_only)
        for parsed, raw in zip(markets, wire["markets"], strict=True):
            for candle, raw_candle in zip(
                parsed.candlesticks, raw["candlesticks"], strict=True
            ):
                assert str(candle.volume_fp) == raw_candle["volume_fp"]
                end_ts = int(candle.end_period_ts.timestamp())
                assert end_ts == raw_candle["end_period_ts"]


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


def fixture_body(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def historical_harness() -> Harness:
    """The routed harness plus the three ``/historical/*`` paths, each serving
    the fixture Task 3.2 recorded — no hand-rolled bodies (slice 267)."""
    candles = fixture_body("historical_candles_market")
    return build_harness(
        {
            **ROUTES,
            HISTORICAL_MARKETS_PATH: fixture_body("historical_markets_page"),
            HISTORICAL_TRADES_PATH: fixture_body("historical_trades_window"),
            HISTORICAL_MARKET_CANDLESTICKS_PATH.format(
                ticker=candles["ticker"]
            ): candles,
        }
    )


class TestHistoricalEndpoints:
    """Slice 267, Task 3.3: the three archive methods route, query, and parse
    exactly as their live mirrors do."""

    async def test_markets_path_query_and_page(self, historical_harness: Harness):
        page = await historical_harness.client.get_historical_markets(
            limit=MARKETS_PAGE_LIMIT, mve_filter=KALSHI_MVE_FILTER, cursor="c1"
        )
        assert historical_harness.last.url.path == "/trade-api/v2/historical/markets"
        assert historical_harness.query() == {
            "limit": str(MARKETS_PAGE_LIMIT),
            "mve_filter": KALSHI_MVE_FILTER,
            "cursor": "c1",
        }
        assert isinstance(page, MarketsPage)
        assert len(page.markets) == MARKETS_PAGE_LIMIT and page.cursor

    async def test_trades_query_matches_the_live_method(
        self, historical_harness: Harness
    ):
        client = historical_harness.client
        await client.get_trades(min_ts=1, max_ts=2, limit=100, cursor="c2")
        live = historical_harness.query()
        page = await client.get_historical_trades(
            min_ts=1, max_ts=2, limit=100, cursor="c2"
        )
        assert historical_harness.last.url.path == "/trade-api/v2/historical/trades"
        assert historical_harness.query() == live
        assert historical_harness.query() == {
            "min_ts": "1",
            "max_ts": "2",
            "limit": "100",
            "cursor": "c2",
        }
        assert isinstance(page, TradesPage) and isinstance(page.trades[0], Trade)

    async def test_candles_path_and_the_three_params_only(
        self, historical_harness: Harness
    ):
        ticker = fixture_body("historical_candles_market")["ticker"]
        candles = await historical_harness.client.get_historical_market_candlesticks(
            ticker,
            start_ts=1787400000,
            end_ts=1787486400,
            period_interval=CandlePeriod.MINUTE,
        )
        assert (
            historical_harness.last.url.path
            == f"/trade-api/v2/historical/markets/{ticker}/candlesticks"
        )
        assert historical_harness.query() == {
            "start_ts": "1787400000",
            "end_ts": "1787486400",
            "period_interval": "1",
        }
        assert candles and isinstance(candles[0], Candlestick)
        assert candles[0].volume_fp > 0

    async def test_unrouted_historical_path_is_permanent_error(
        self, historical_harness: Harness
    ):
        with pytest.raises(ProviderPermanentError):
            await historical_harness.client.get_historical_market_candlesticks(
                "NOPE", start_ts=1, end_ts=2, period_interval=CandlePeriod.MINUTE
            )
