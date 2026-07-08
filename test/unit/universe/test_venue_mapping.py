"""Unit tests for venue_mapping module."""

from __future__ import annotations

import logging

import pytest

from manta_trading.data.universe.venue_mapping import (
    is_non_us_exchange,
    map_finnhub_exchange,
)


class TestMapFinnhubExchange:
    def test_nasdaq_nms(self):
        venue, cal = map_finnhub_exchange("NASDAQ NMS - GLOBAL MARKET")
        assert venue == "NASDAQ"
        assert cal == "NASDAQ"

    def test_nasdaq_capital(self):
        venue, cal = map_finnhub_exchange("NASDAQ CAPITAL MARKETS")
        assert venue == "NASDAQ"
        assert cal == "NASDAQ"

    def test_nyse(self):
        venue, cal = map_finnhub_exchange("NEW YORK STOCK EXCHANGE, INC.")
        assert venue == "NYSE"
        assert cal == "NYSE"

    def test_nyse_arca(self):
        venue, cal = map_finnhub_exchange("NYSE ARCA")
        assert venue == "NYSE_ARCA"
        assert cal == "NYSE"

    def test_nyse_mkt(self):
        venue, cal = map_finnhub_exchange("NYSE MKT LLC")
        assert venue == "NYSE_MKT"
        assert cal == "NYSE"

    def test_nyse_american(self):
        venue, cal = map_finnhub_exchange("NYSE AMERICAN")
        assert venue == "NYSE_MKT"
        assert cal == "NYSE"

    def test_bats(self):
        venue, cal = map_finnhub_exchange("BATS EXCHANGE")
        assert venue == "BATS"
        assert cal == "NYSE"

    def test_cboe_bzx(self):
        venue, cal = map_finnhub_exchange("CBOE BZX U.S. EQUITIES EXCHANGE")
        assert venue == "BATS"
        assert cal == "NYSE"

    def test_unknown_returns_fallback(self, caplog):
        with caplog.at_level(logging.WARNING):
            venue, cal = map_finnhub_exchange("TORONTO STOCK EXCHANGE")
        assert venue == "US"
        assert cal == "NYSE"
        assert "unrecognized" in caplog.text.lower()

    def test_empty_string_returns_fallback(self, caplog):
        with caplog.at_level(logging.WARNING):
            venue, cal = map_finnhub_exchange("")
        assert venue == "US"
        assert cal == "NYSE"
        # Empty string should not produce a warning (no point warning on blank)
        assert "unrecognized" not in caplog.text.lower()

    @pytest.mark.parametrize("exchange,expected_venue,expected_cal", [
        ("NASDAQ NMS - GLOBAL MARKET", "NASDAQ", "NASDAQ"),
        ("NEW YORK STOCK EXCHANGE, INC.", "NYSE", "NYSE"),
        ("NYSE ARCA", "NYSE_ARCA", "NYSE"),
        ("NYSE MKT LLC", "NYSE_MKT", "NYSE"),
        ("BATS EXCHANGE", "BATS", "NYSE"),
    ])
    def test_parametrized_known_venues(self, exchange: str, expected_venue: str, expected_cal: str):
        venue, cal = map_finnhub_exchange(exchange)
        assert venue == expected_venue
        assert cal == expected_cal


class TestIsNonUsExchange:
    @pytest.mark.parametrize("exchange", [
        "TORONTO STOCK EXCHANGE",
        "TSX VENTURE EXCHANGE - NEX",
        "CANADIAN NATIONAL STOCK EXCHANGE",
        "HONG KONG EXCHANGES AND CLEARING LTD",
        "TOKYO STOCK EXCHANGE-TOKYO PRO MARKET",
        "INDONESIA STOCK EXCHANGE",
        "XETRA",
        "Euronext Amsterdam",
        "London Stock Exchange",
        "Australian Securities Exchange",
        "Bombay Stock Exchange",
    ])
    def test_known_non_us_returns_true(self, exchange: str):
        assert is_non_us_exchange(exchange) is True

    @pytest.mark.parametrize("exchange", [
        "NASDAQ NMS - GLOBAL MARKET",
        "NEW YORK STOCK EXCHANGE, INC.",
        "NYSE ARCA",
        "BATS EXCHANGE",
        "OTC MARKETS",
        "",
    ])
    def test_us_or_unknown_returns_false(self, exchange: str):
        assert is_non_us_exchange(exchange) is False
