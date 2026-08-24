"""Tests for Kalshi domain constants and enums (slice 261, Task 1.3)."""

from __future__ import annotations

from datetime import timedelta
from enum import IntEnum, StrEnum
from pathlib import Path

import httpx

from manta_trading.data.kalshi import constants as kc
from manta_trading.providers.types import RateLimit

_SRC_ROOT = Path(__file__).resolve().parents[4] / "src" / "manta_trading"


class TestEnums:
    def test_market_status_served_values(self):
        assert issubclass(kc.MarketStatus, StrEnum)
        assert [m.value for m in kc.MarketStatus] == [
            "initialized",
            "active",
            "inactive",
            "closed",
            "determined",
            "finalized",
        ]

    def test_market_status_filter_values(self):
        assert issubclass(kc.MarketStatusFilter, StrEnum)
        assert [m.value for m in kc.MarketStatusFilter] == [
            "unopened",
            "open",
            "paused",
            "closed",
            "settled",
        ]

    def test_event_status_filter_values(self):
        assert issubclass(kc.EventStatusFilter, StrEnum)
        assert [m.value for m in kc.EventStatusFilter] == [
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


class TestCatalogSyncConstants:
    """Slice 262 sync constants (task 1.1)."""

    def test_walk_filters_are_live_statuses_only(self):
        assert all(
            isinstance(f, kc.MarketStatusFilter) for f in kc.CATALOG_WALK_FILTERS
        )
        assert kc.MarketStatusFilter.SETTLED not in kc.CATALOG_WALK_FILTERS
        assert set(kc.CATALOG_WALK_FILTERS) == set(kc.MarketStatusFilter) - {
            kc.MarketStatusFilter.SETTLED
        }

    def test_mve_filter_value(self):
        assert kc.KALSHI_MVE_FILTER == "exclude"

    def test_page_and_batch_sizes(self):
        assert kc.MARKETS_PAGE_LIMIT == 1000
        assert kc.EVENTS_PAGE_LIMIT == 200
        assert kc.TICKERS_BATCH_SIZE == 100

    def test_settled_window_and_overlap(self):
        assert kc.SETTLED_WINDOW == timedelta(hours=6)
        assert kc.WINDOW_OVERLAP == timedelta(seconds=1)

    def test_age_buckets_reference_stuck_threshold(self):
        assert kc.KALSHI_SETTLEMENT_STUCK_AFTER == timedelta(days=7)
        assert kc.KALSHI_SETTLEMENT_STUCK_AFTER in kc.AWAITING_AGE_BUCKETS

    def test_age_buckets_strictly_increasing(self):
        buckets = kc.AWAITING_AGE_BUCKETS
        assert all(a < b for a, b in zip(buckets, buckets[1:], strict=False))

    def test_db_preflight_constants(self):
        assert isinstance(kc.DB_CONNECT_TIMEOUT_SECONDS, int)
        assert kc.DB_CONNECT_TIMEOUT_SECONDS > 0
        assert isinstance(kc.SYNC_ADVISORY_LOCK_KEY, int)
