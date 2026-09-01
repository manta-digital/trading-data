"""Fixture-driven client coverage (slice 261, Task 6.3).

Every committed file under ``test/fixtures/kalshi/`` is served to
``KalshiClient`` through ``httpx.MockTransport`` exactly as recorded, so the
parsers are tested against the real production format. ``Decimal`` fields
are asserted against the fixture strings; pagination follows the genuine
two-page cursor pairs; the recorded 404 body drives the permanent-error path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    HISTORICAL_MARKET_CANDLESTICKS_PATH,
    HISTORICAL_MARKETS_PATH,
    HISTORICAL_TRADES_PATH,
    MARKETS_PAGE_LIMIT,
    CandlePeriod,
    MarketStatus,
)
from manta_trading.data.kalshi.models import MarketsPage, TradesPage
from manta_trading.providers.errors import ProviderPermanentError

FIXTURE_DIR = Path(__file__).resolve().parents[4] / "test" / "fixtures" / "kalshi"
EXPECTED_FIXTURES = {
    "series_list",
    "series",
    "events_page1",
    "events_page2",
    "event",
    "markets_page1",
    "markets_page2",
    "markets_open",
    "market",
    "candlesticks",
    "trades_page1",
    "trades_page2",
    "historical_cutoff",
    "error_404",
    # slice 262
    "markets_by_tickers",
    "events_by_tickers",
    "markets_settled_window",
    # slice 264
    "candlesticks_batch",
    "error_400_candles_cap",
    # slice 265
    "trades_window",
    "trades_window_last",
    "trades_empty",
    # slice 267
    "historical_markets_page",
    "historical_trades_window",
    "historical_trades_window_last",
    "historical_candles_market",
}


async def take[T](iterator: AsyncIterator[T], count: int) -> list[T]:
    """First ``count`` items; closes the generator before it fetches further."""
    items: list[T] = []
    try:
        async for item in iterator:
            items.append(item)
            if len(items) == count:
                break
    finally:
        if isinstance(iterator, AsyncGenerator):
            await iterator.aclose()
    return items


def cursors(requests: list[httpx.Request]) -> list[str | None]:
    return [r.url.params.get("cursor") for r in requests]


def raw(name: str) -> bytes:
    return (FIXTURE_DIR / f"{name}.json").read_bytes()


def body(name: str) -> dict[str, Any]:
    return json.loads(raw(name))


Spec = tuple[str, int, str] | tuple[str, int, str, dict[str, str]]


def serve(*specs: Spec, seen: list[httpx.Request] | None = None) -> KalshiClient:
    """Client whose transport serves recorded bodies by path (+ optional query).

    Each spec is ``(path, status, fixture_name[, required_query_subset])``;
    the first spec whose path matches and whose query subset is present wins.
    ``seen`` collects every request for assertions on the call sequence.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path.removeprefix("/trade-api/v2")
        params = dict(request.url.params)
        for spec in specs:
            spec_path, status, name = spec[0], spec[1], spec[2]
            wanted = spec[3] if len(spec) == 4 else {}
            if path == spec_path and all(params.get(k) == v for k, v in wanted.items()):
                return httpx.Response(
                    status,
                    content=raw(name),
                    headers={"content-type": "application/json"},
                )
        raise AssertionError(f"unrouted request in test: {request.url}")

    return KalshiClient(transport=httpx.MockTransport(handler))


def test_fixture_set_is_complete_and_valid_json():
    present = {p.stem for p in FIXTURE_DIR.glob("*.json")}
    assert present == EXPECTED_FIXTURES
    for name in present:
        json.loads(raw(name))


class TestSeries:
    async def test_series_list_is_unpaginated(self):
        client = serve(("/series", 200, "series_list"))
        series = await client.get_series_list()
        assert "cursor" not in body("series_list")
        assert len(series) == len(body("series_list")["series"])
        assert all(s.ticker for s in series)

    async def test_series_decimal_and_timestamp(self):
        client = serve(("/series/FED", 200, "series"))
        series = await client.get_series("FED")
        wire = body("series")["series"]
        assert series.ticker == wire["ticker"]
        assert series.fee_multiplier == Decimal(str(wire["fee_multiplier"]))
        assert series.last_updated_ts is not None
        assert series.last_updated_ts.tzinfo is not None


class TestEvents:
    async def test_iter_events_follows_recorded_cursor(self):
        page1, page2 = body("events_page1"), body("events_page2")
        seen: list[httpx.Request] = []
        client = serve(
            ("/events", 200, "events_page2", {"cursor": page1["cursor"]}),
            ("/events", 200, "events_page1"),
            seen=seen,
        )
        expected = [e["event_ticker"] for e in page1["events"] + page2["events"]]
        got = await take(client.iter_events(limit=5), len(expected))
        assert [e.event_ticker for e in got] == expected
        assert cursors(seen) == [None, page1["cursor"]]
        assert page2["cursor"]  # a real listing continues; termination is unit-tested

    async def test_single_event_nested_markets(self):
        """Recorded shape: with ``with_nested_markets=true`` the markets sit
        *inside* ``event`` and the top-level ``markets`` list is empty."""
        wire = body("event")
        ticker = wire["event"]["event_ticker"]
        client = serve((f"/events/{ticker}", 200, "event"))
        event = await client.get_event(ticker, with_nested_markets=True)
        assert event.series_ticker == wire["event"]["series_ticker"]
        assert event.markets is not None
        assert wire["markets"] == []
        assert len(event.markets) == len(wire["event"]["markets"])


class TestMarkets:
    async def test_iter_markets_follows_recorded_cursor(self):
        page1, page2 = body("markets_page1"), body("markets_page2")
        seen: list[httpx.Request] = []
        client = serve(
            ("/markets", 200, "markets_page2", {"cursor": page1["cursor"]}),
            ("/markets", 200, "markets_page1"),
            seen=seen,
        )
        expected = [m["ticker"] for m in page1["markets"] + page2["markets"]]
        got = await take(client.iter_markets(limit=5), len(expected))
        assert [m.ticker for m in got] == expected
        assert cursors(seen) == [None, page1["cursor"]]

    @pytest.mark.parametrize("name", ["markets_page1", "markets_page2", "markets_open"])
    async def test_served_status_values_are_in_market_status(self, name: str):
        client = serve(("/markets", 200, name))
        page = await client.get_markets()
        served = {m.status for m in page.markets}
        assert served <= {s.value for s in MarketStatus}

    async def test_settled_market_has_result_and_settlement_ts(self):
        client = serve(("/markets", 200, "markets_page1"))
        page = await client.get_markets()
        settled = [m for m in page.markets if m.status == MarketStatus.FINALIZED]
        assert settled
        assert all(m.result in {"yes", "no"} for m in settled)
        assert all(m.settlement_ts is not None for m in settled)

    @pytest.mark.parametrize(
        "field",
        [
            "notional_value_dollars",
            "last_price_dollars",
            "yes_bid_dollars",
            "yes_ask_dollars",
            "no_bid_dollars",
            "no_ask_dollars",
            "liquidity_dollars",
            "volume_fp",
            "volume_24h_fp",
            "open_interest_fp",
            "settlement_value_dollars",
        ],
    )
    async def test_market_decimals_match_wire_strings(self, field: str):
        wire = body("market")["market"]
        client = serve((f"/markets/{wire['ticker']}", 200, "market"))
        market = await client.get_market(wire["ticker"])
        assert str(getattr(market, field)) == wire[field]


class TestCandlesticks:
    async def test_candles_parse_with_ohlc(self):
        wire = body("candlesticks")
        ticker = wire["ticker"]
        client = serve(
            (f"/series/S/markets/{ticker}/candlesticks", 200, "candlesticks")
        )
        candles = await client.get_market_candlesticks(
            "S", ticker, start_ts=0, end_ts=1, period_interval=CandlePeriod.HOUR
        )
        assert len(candles) == len(wire["candlesticks"])
        assert len(candles) > 0
        for parsed, raw_candle in zip(candles, wire["candlesticks"], strict=True):
            assert str(parsed.volume_fp) == raw_candle["volume_fp"]
            assert int(parsed.end_period_ts.timestamp()) == raw_candle["end_period_ts"]
            for key, value in raw_candle["price"].items():
                assert str(getattr(parsed.price, key)) == value
            for key, value in raw_candle["yes_bid"].items():
                assert str(getattr(parsed.yes_bid, key)) == value


class TestTrades:
    async def test_iter_trades_follows_recorded_cursor(self):
        page1, page2 = body("trades_page1"), body("trades_page2")
        seen: list[httpx.Request] = []
        client = serve(
            ("/markets/trades", 200, "trades_page2", {"cursor": page1["cursor"]}),
            ("/markets/trades", 200, "trades_page1"),
            seen=seen,
        )
        expected = [t["trade_id"] for t in page1["trades"] + page2["trades"]]
        got = await take(client.iter_trades(limit=5), len(expected))
        assert [t.trade_id for t in got] == expected
        assert cursors(seen) == [None, page1["cursor"]]

    async def test_trade_decimals_match_wire_strings(self):
        client = serve(("/markets/trades", 200, "trades_page1"))
        page = await client.get_trades()
        for parsed, raw_trade in zip(
            page.trades, body("trades_page1")["trades"], strict=True
        ):
            assert str(parsed.count_fp) == raw_trade["count_fp"]
            assert str(parsed.yes_price_dollars) == raw_trade["yes_price_dollars"]
            assert str(parsed.no_price_dollars) == raw_trade["no_price_dollars"]


class TestTradesWindow:
    """Slice 265, Task 4.5: the windowed pages the trades core walks."""

    @pytest.mark.parametrize(
        ("name", "count", "has_cursor"),
        [
            ("trades_window", 100, True),
            ("trades_window_last", 45, False),
            ("trades_empty", 0, False),
        ],
    )
    async def test_each_parses_into_a_trades_page(
        self, name: str, count: int, has_cursor: bool
    ):
        client = serve(("/markets/trades", 200, name))
        page = await client.get_trades(min_ts=1, max_ts=2, limit=100)
        assert len(page.trades) == count
        assert bool(page.cursor) is has_cursor
        for parsed, wire in zip(page.trades, body(name)["trades"], strict=True):
            assert parsed.trade_id == wire["trade_id"]
            assert parsed.is_block_trade == wire["is_block_trade"]

    async def test_an_empty_cursor_terminates_the_walk(self):
        """The fact the core's page loop depends on: the last page carries
        ``cursor: ""`` and no further request is made."""
        first = body("trades_window")
        seen: list[httpx.Request] = []
        client = serve(
            ("/markets/trades", 200, "trades_window_last", {"cursor": first["cursor"]}),
            ("/markets/trades", 200, "trades_window"),
            seen=seen,
        )
        got = await take(client.iter_trades(min_ts=1, max_ts=2, limit=100), 10_000)
        assert len(got) == 100 + 45
        assert cursors(seen) == [None, first["cursor"]]
        assert body("trades_window_last")["cursor"] == ""
        assert body("trades_empty") == {"cursor": "", "trades": []}


class TestHistoricalCutoff:
    async def test_cutoff_shape(self):
        client = serve(("/historical/cutoff", 200, "historical_cutoff"))
        cutoff = await client.get_historical_cutoff()
        wire = body("historical_cutoff")
        assert (
            cutoff.market_settled_ts.isoformat().replace("+00:00", "Z")
            == wire["market_settled_ts"]
        )
        assert (
            cutoff.trades_created_ts.isoformat().replace("+00:00", "Z")
            == wire["trades_created_ts"]
        )


class TestErrorBody:
    async def test_recorded_404_is_permanent_error(self):
        client = serve(("/markets/NOPE-NOT-A-TICKER", 404, "error_404"))
        with pytest.raises(ProviderPermanentError):
            await client.get_market("NOPE-NOT-A-TICKER")

    async def test_recorded_candle_cap_400_is_permanent_error(self):
        """Slice 264, Decision 7: a request over the batch candle cap is a
        400 — permanent, never retried — so on the phase's path it is a
        planner bug that must surface, not a provider condition."""
        wire = body("error_400_candles_cap")
        assert "max candlesticks: 10000" in wire["error"]["details"]
        client = serve(("/markets/candlesticks", 400, "error_400_candles_cap"))
        with pytest.raises(ProviderPermanentError):
            await client.get_markets_candlesticks(
                ["ANY"], start_ts=0, end_ts=1, period_interval=CandlePeriod.MINUTE
            )


def recorder_module() -> Any:
    """The recorder script, loaded by path (``scripts/`` is not a package),
    so its request constants stay defined in exactly one place."""
    import importlib.util

    path = FIXTURE_DIR.parents[2] / "scripts" / "record_kalshi_fixtures.py"
    spec = importlib.util.spec_from_file_location("record_kalshi_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSliceBatchFixtures:
    """Slice 262 recorder targets: ``tickers`` batches and one settled window."""

    async def test_markets_by_tickers_omits_the_bogus_ticker(self):
        rec = recorder_module()
        client = serve(("/markets", 200, "markets_by_tickers"))
        page = await client.get_markets(tickers="ignored-by-the-mock")
        requested = rec.TICKERS_BATCH_SAMPLE + 1
        recorded = {m["ticker"] for m in body("markets_settled_window")["markets"]}
        assert 0 < len(page.markets) < requested
        assert {m.ticker for m in page.markets} <= recorded
        assert rec.UNKNOWN_TICKER not in {m.ticker for m in page.markets}

    async def test_events_by_tickers_parses(self):
        rec = recorder_module()
        client = serve(("/events", 200, "events_by_tickers"))
        page = await client.get_events(tickers="ignored-by-the-mock")
        recorded = {e["event_ticker"] for e in body("events_page1")["events"]}
        assert 0 < len(page.events) <= rec.TICKERS_BATCH_SAMPLE
        assert {e.event_ticker for e in page.events} <= recorded

    async def test_settled_window_is_finalized_non_mve_within_span(self):
        SETTLED_WINDOW_SPAN = recorder_module().SETTLED_WINDOW_SPAN
        client = serve(("/markets", 200, "markets_settled_window"))
        page = await client.get_markets(min_settled_ts=0, max_settled_ts=1)
        assert page.markets
        assert all(m.status == MarketStatus.FINALIZED for m in page.markets)
        assert all(m.mve_collection_ticker is None for m in page.markets)
        stamps = [m.settlement_ts for m in page.markets if m.settlement_ts is not None]
        assert len(stamps) == len(page.markets)
        assert max(stamps) - min(stamps) <= SETTLED_WINDOW_SPAN


class TestHistoricalFixtures:
    """Slice 267, Task 3.2: the archive page, one archived-tape window pair,
    and one archived market's candles — recorded 20260901, public mode."""

    async def test_archive_page_is_a_full_finalized_page_with_a_cursor(self):
        client = serve((HISTORICAL_MARKETS_PATH, 200, "historical_markets_page"))
        page = await client.get_historical_markets(limit=MARKETS_PAGE_LIMIT)
        assert isinstance(page, MarketsPage)
        assert len(page.markets) == MARKETS_PAGE_LIMIT
        assert page.cursor
        assert {m.status for m in page.markets} <= {s.value for s in MarketStatus}
        assert all(m.settlement_ts is not None for m in page.markets)
        assert all(m.mve_collection_ticker is None for m in page.markets)

    async def test_archive_page_is_served_coarsely_newest_first(self):
        """Decision 9's stop rule reads the page's settlement order: the
        first market settled after the last, with overlap allowed inside."""
        client = serve((HISTORICAL_MARKETS_PATH, 200, "historical_markets_page"))
        page = await client.get_historical_markets()
        stamps = [m.settlement_ts for m in page.markets if m.settlement_ts]
        assert stamps[0] > stamps[-1]

    @pytest.mark.parametrize(
        ("name", "has_cursor"),
        [("historical_trades_window", True), ("historical_trades_window_last", False)],
    )
    async def test_each_archived_page_parses_into_a_trades_page(
        self, name: str, has_cursor: bool
    ):
        client = serve((HISTORICAL_TRADES_PATH, 200, name))
        page = await client.get_historical_trades(min_ts=1, max_ts=2, limit=100)
        assert isinstance(page, TradesPage)
        assert len(page.trades) == len(body(name)["trades"]) > 0
        assert bool(page.cursor) is has_cursor
        for parsed, wire in zip(page.trades, body(name)["trades"], strict=True):
            assert parsed.trade_id == wire["trade_id"]
            assert str(parsed.count_fp) == wire["count_fp"]

    def test_archived_trade_has_the_live_trade_field_set(self):
        """261 Discovery's "same shape as live trades", proven on the wire."""
        archived = set(body("historical_trades_window")["trades"][0])
        live = set(body("trades_window")["trades"][0])
        assert archived == live

    async def test_candles_parse_with_ohlc_through_the_legacy_shape(self):
        wire = body("historical_candles_market")
        client = serve(
            (
                HISTORICAL_MARKET_CANDLESTICKS_PATH.format(ticker=wire["ticker"]),
                200,
                "historical_candles_market",
            )
        )
        candles = await client.get_historical_market_candlesticks(
            wire["ticker"], start_ts=1, end_ts=2, period_interval=CandlePeriod.MINUTE
        )
        assert len(candles) == len(wire["candlesticks"]) > 0
        first_wire = wire["candlesticks"][0]
        # The endpoint serves the legacy names; the client maps them.
        assert {"volume", "open_interest", "price"} <= set(first_wire)
        assert "volume_fp" not in first_wire
        assert set(first_wire["price"]) <= {
            "open",
            "high",
            "low",
            "close",
            "previous",
            "mean",
        }
        first = candles[0]
        assert str(first.volume_fp) == first_wire["volume"]
        assert str(first.price.close_dollars) == first_wire["price"]["close"]
        assert str(first.price.open_dollars) == first_wire["price"]["open"]
        assert str(first.yes_bid.high_dollars) == first_wire["yes_bid"]["high"]
        assert first.open_interest_fp is not None

    def test_candles_fixture_is_the_recorded_window_around_the_first_trade(self):
        """The recorder pairs the candles with ``historical_trades_window``'s
        first trade; the fixture's ticker proves the pair is consistent."""
        first_trade = body("historical_trades_window")["trades"][0]
        assert body("historical_candles_market")["ticker"] == first_trade["ticker"]
