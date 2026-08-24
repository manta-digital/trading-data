"""Tests for Kalshi domain constants and enums (slice 261, Task 1.3)."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from pathlib import Path

import httpx

from manta_trading.data.kalshi import constants as kc
from manta_trading.providers.types import RateLimit

_SRC_ROOT = Path(__file__).resolve().parents[4] / "src" / "manta_trading"


class TestEnums:
    def test_market_status_values(self):
        assert issubclass(kc.MarketStatus, StrEnum)
        assert [m.value for m in kc.MarketStatus] == [
            "unopened",
            "open",
            "paused",
            "closed",
            "settled",
        ]

    def test_event_status_values(self):
        assert issubclass(kc.EventStatus, StrEnum)
        assert [m.value for m in kc.EventStatus] == [
            "unopened",
            "open",
            "closed",
            "settled",
        ]

    def test_candle_period_values(self):
        assert issubclass(kc.CandlePeriod, IntEnum)
        assert [p.value for p in kc.CandlePeriod] == [1, 60, 1440]

    def test_surface_values(self):
        assert issubclass(kc.Surface, StrEnum)
        assert [s.value for s in kc.Surface] == ["catalog", "candlesticks", "trades"]


class TestRateLimits:
    def test_public_budget(self):
        assert isinstance(kc.KALSHI_PUBLIC_RATE_LIMIT, RateLimit)
        assert kc.KALSHI_PUBLIC_RATE_LIMIT.requests_per_minute == 300

    def test_authenticated_budget(self):
        assert isinstance(kc.KALSHI_AUTHENTICATED_RATE_LIMIT, RateLimit)
        assert kc.KALSHI_AUTHENTICATED_RATE_LIMIT.requests_per_minute == 1000


class TestTimeout:
    def test_all_four_phases_explicit(self):
        timeout = kc.KALSHI_REQUEST_TIMEOUT
        assert isinstance(timeout, httpx.Timeout)
        for phase in ("connect", "read", "write", "pool"):
            assert getattr(timeout, phase) is not None, phase


class TestEndpointPaths:
    def test_base_url(self):
        assert kc.KALSHI_BASE_URL == "https://external-api.kalshi.com/trade-api/v2"

    def test_paths_match_design(self):
        assert kc.SERIES_LIST_PATH == "/series"
        assert kc.SERIES_PATH == "/series/{series_ticker}"
        assert kc.EVENTS_PATH == "/events"
        assert kc.EVENT_PATH == "/events/{event_ticker}"
        assert kc.MARKETS_PATH == "/markets"
        assert kc.MARKET_PATH == "/markets/{ticker}"
        assert (
            kc.MARKET_CANDLESTICKS_PATH
            == "/series/{series_ticker}/markets/{ticker}/candlesticks"
        )
        assert kc.TRADES_PATH == "/markets/trades"
        assert kc.HISTORICAL_CUTOFF_PATH == "/historical/cutoff"


class TestEnvVarNames:
    def test_names(self):
        assert kc.KALSHI_API_KEY_ID_ENV == "MT_KALSHI_API_KEY_ID"
        assert kc.KALSHI_PRIVATE_KEY_PATH_ENV == "MT_KALSHI_PRIVATE_KEY_PATH"


class TestSingleSourceOfTruth:
    """The base URL and ``trade-api`` paths appear in exactly one source module."""

    def test_base_url_defined_once_in_src(self):
        needle = "external-api.kalshi.com"
        hits = sorted(
            p.relative_to(_SRC_ROOT).as_posix()
            for p in _SRC_ROOT.rglob("*.py")
            if needle in p.read_text(encoding="utf-8")
        )
        assert hits == ["data/kalshi/constants.py"]
