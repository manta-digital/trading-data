"""Model tests against hand-fetched live samples (slice 261, Task 3.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from kalshi_support.samples import (
    CANDLE_SAMPLE,
    CUTOFF_SAMPLE,
    EVENT_SAMPLE,
    MARKET_SAMPLE,
    SERIES_SAMPLE,
    TRADE_SAMPLE,
)
from pydantic import ValidationError

from manta_trading.data.kalshi import models as km


class TestSeries:
    def test_parses(self):
        series = km.Series.model_validate(SERIES_SAMPLE)
        assert series.ticker == "FED"
        assert series.fee_multiplier == Decimal("1")
        assert series.tags == ["Interest rates"]
        assert series.settlement_sources is not None
        assert series.settlement_sources[0].name == "Federal Reserve Board of Governors"
        assert series.last_updated_ts == datetime(
            2026, 2, 26, 8, 50, 31, 901806, tzinfo=UTC
        )

    def test_list_wrapper(self):
        page = km.SeriesListResponse.model_validate({"series": [SERIES_SAMPLE]})
        assert len(page.series) == 1


class TestEvent:
    def test_parses(self):
        event = km.Event.model_validate(EVENT_SAMPLE)
        assert event.event_ticker == "KXELONMARS-99"
        assert event.series_ticker == "KXELONMARS"
        assert event.mutually_exclusive is False
        assert event.markets is None

    def test_nested_markets(self):
        payload = {**EVENT_SAMPLE, "markets": [MARKET_SAMPLE]}
        event = km.Event.model_validate(payload)
        assert event.markets is not None
        assert event.markets[0].ticker == MARKET_SAMPLE["ticker"]

    def test_page_wrapper_with_cursor(self):
        page = km.EventsPage.model_validate(
            {"events": [EVENT_SAMPLE], "cursor": "abc", "milestones": []}
        )
        assert page.cursor == "abc"

    def test_page_wrapper_without_cursor(self):
        page = km.EventsPage.model_validate({"events": []})
        assert page.cursor is None


class TestMarket:
    def test_parses_settled_market(self):
        market = km.Market.model_validate(MARKET_SAMPLE)
        assert market.status == "finalized"
        assert market.result == "no"
        assert market.settlement_ts == datetime(
            2026, 8, 24, 15, 1, 53, 258486, tzinfo=UTC
        )
        assert market.close_time.tzinfo is not None

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            ("notional_value_dollars", Decimal("1.0000")),
            ("no_ask_dollars", Decimal("1.0000")),
            ("last_price_dollars", Decimal("0.0000")),
            ("open_interest_fp", Decimal("0.00")),
            ("volume_fp", Decimal("0.00")),
            ("settlement_value_dollars", Decimal("0.0000")),
        ],
    )
    def test_decimal_fields_exact(self, field: str, expected: Decimal):
        market = km.Market.model_validate(MARKET_SAMPLE)
        value = getattr(market, field)
        assert value == expected
        assert str(value) == MARKET_SAMPLE[field]

    def test_extra_fields_tolerated(self):
        market = km.Market.model_validate({**MARKET_SAMPLE, "brand_new_field": 1})
        assert market.model_extra is not None
        assert market.model_extra["brand_new_field"] == 1
        # Fields the schema does not model are still present as extras.
        assert market.model_extra["settlement_timer_seconds"] == 5
        assert market.model_extra["price_ranges"] == MARKET_SAMPLE["price_ranges"]

    def test_missing_required_raises(self):
        payload = dict(MARKET_SAMPLE)
        del payload["event_ticker"]
        with pytest.raises(ValidationError):
            km.Market.model_validate(payload)

    def test_malformed_decimal_raises(self):
        with pytest.raises(ValidationError):
            km.Market.model_validate({**MARKET_SAMPLE, "volume_fp": "lots"})


class TestTrade:
    def test_parses(self):
        trade = km.Trade.model_validate(TRADE_SAMPLE)
        assert trade.count_fp == Decimal("43.00")
        assert trade.yes_price_dollars == Decimal("0.0800")
        assert trade.no_price_dollars == Decimal("0.9200")
        assert trade.created_time.tzinfo is not None
        assert trade.taker_book_side == "bid"

    def test_page_wrapper(self):
        page = km.TradesPage.model_validate({"trades": [TRADE_SAMPLE], "cursor": "x"})
        assert page.cursor == "x"

    def test_missing_required_raises(self):
        payload = dict(TRADE_SAMPLE)
        del payload["trade_id"]
        with pytest.raises(ValidationError):
            km.Trade.model_validate(payload)


class TestCandlestick:
    def test_parses_unix_seconds_to_aware_datetime(self):
        candle = km.Candlestick.model_validate(CANDLE_SAMPLE)
        assert candle.end_period_ts == datetime.fromtimestamp(1787504400, tz=UTC)

    def test_nested_ohlc_decimals(self):
        candle = km.Candlestick.model_validate(CANDLE_SAMPLE)
        assert candle.yes_bid.open_dollars == Decimal("0.1000")
        assert candle.yes_ask.high_dollars == Decimal("0.1200")
        assert candle.open_interest_fp == Decimal("40101.16")

    def test_price_with_only_previous(self):
        candle = km.Candlestick.model_validate(CANDLE_SAMPLE)
        assert candle.price.previous_dollars == Decimal("0.1000")
        assert candle.price.open_dollars is None
        assert candle.price.close_dollars is None

    def test_response_wrapper(self):
        resp = km.CandlesticksResponse.model_validate(
            {"ticker": "KXELONMARS-99", "candlesticks": [CANDLE_SAMPLE]}
        )
        assert resp.ticker == "KXELONMARS-99"
        assert len(resp.candlesticks) == 1


class TestHistoricalCutoff:
    def test_parses(self):
        cutoff = km.HistoricalCutoff.model_validate(CUTOFF_SAMPLE)
        assert cutoff.market_settled_ts == datetime(2026, 6, 25, tzinfo=UTC)
        assert cutoff.trades_created_ts == datetime(2026, 6, 25, tzinfo=UTC)

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            km.HistoricalCutoff.model_validate(
                {"orders_updated_ts": "2026-06-25T00:00:00Z"}
            )
