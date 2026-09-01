"""Tests for Kalshi domain constants and enums (slice 261, Task 1.3)."""

from __future__ import annotations

import string
from datetime import UTC, timedelta
from enum import IntEnum, StrEnum
from pathlib import Path

import httpx
import pytest

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
            "amended",
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
        assert [s.value for s in kc.Surface] == [
            "catalog",
            "candlesticks",
            "trades",
            "historical",
        ]


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


class TestCandleCollectionConstants:
    """Slice 264 candle-phase constants (Task 1.1): the design's values."""

    def test_batch_endpoint_path(self):
        assert kc.MARKETS_CANDLESTICKS_PATH == "/markets/candlesticks"

    def test_collected_period_is_one_minute(self):
        assert kc.COLLECTED_CANDLE_PERIOD is kc.CandlePeriod.MINUTE

    def test_endpoint_caps(self):
        assert kc.CANDLE_BATCH_MAX_TICKERS == 100
        assert kc.CANDLE_BATCH_MAX_CANDLES == 10_000
        assert kc.CANDLE_SINGLE_MAX_CANDLES == 5_000

    def test_pass_shape(self):
        assert kc.CANDLE_BACKLOG_REQUESTS_PER_PASS == 1_000
        assert kc.CANDLE_PROGRESS_EVERY_REQUESTS == 100

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("CANDLE_FIRST_SIGHT_LOOKBACK", timedelta(hours=24)),
            ("CANDLE_LAG_STALE_AFTER", timedelta(hours=2)),
            ("KALSHI_CANDLE_CHUNK_INTERVAL", timedelta(days=7)),
            ("KALSHI_CANDLE_COMPRESS_AFTER", timedelta(days=14)),
        ],
    )
    def test_timedelta_constants(self, name: str, expected: timedelta):
        value = getattr(kc, name)
        assert isinstance(value, timedelta)
        assert value == expected


class TestTradeCollectionConstants:
    """Slice 265 trades-phase constants (Task 2.1): the design's values."""

    def test_page_limit_is_the_verified_ceiling(self):
        assert kc.TRADE_PAGE_LIMIT == 1_000

    def test_pass_cap(self):
        assert kc.TRADE_REQUESTS_PER_PASS == 3_000

    def test_window_overlap_is_reused_not_redefined(self):
        """Decision 1 steps each window's lower bound back by 262's overlap."""
        assert kc.WINDOW_OVERLAP == timedelta(seconds=1)
        assert not hasattr(kc, "TRADE_WINDOW_OVERLAP")

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("TRADE_WINDOW", timedelta(hours=1)),
            ("TRADE_LATE_ARRIVAL_GUARD", timedelta(minutes=1)),
            ("TRADE_LAG_STALE_AFTER", timedelta(hours=2)),
            ("KALSHI_TRADE_CHUNK_INTERVAL", timedelta(days=7)),
            ("KALSHI_TRADE_COMPRESS_AFTER", timedelta(days=14)),
        ],
    )
    def test_timedelta_constants(self, name: str, expected: timedelta):
        value = getattr(kc, name)
        assert isinstance(value, timedelta)
        assert value == expected

    def test_guard_and_overlap_are_smaller_than_a_window(self):
        assert kc.WINDOW_OVERLAP < kc.TRADE_LATE_ARRIVAL_GUARD < kc.TRADE_WINDOW


class TestHistoricalBackfillConstants:
    """Slice 267 historical-phase constants (Task 2.2): the design's values,
    and Decision 2's two cap figures asserted where they are derived."""

    def test_paths_match_design(self):
        assert kc.HISTORICAL_MARKETS_PATH == "/historical/markets"
        assert kc.HISTORICAL_TRADES_PATH == "/historical/trades"
        assert (
            kc.HISTORICAL_MARKET_CANDLESTICKS_PATH
            == "/historical/markets/{ticker}/candlesticks"
        )

    def test_candles_path_has_no_series_segment(self):
        """261 Discovery: the historical candles path takes only the ticker."""
        fields = [
            name
            for _, name, _, _ in string.Formatter().parse(
                kc.HISTORICAL_MARKET_CANDLESTICKS_PATH
            )
            if name is not None
        ]
        assert fields == ["ticker"]

    def test_surface_gains_historical_last(self):
        assert len(kc.Surface) == 4
        assert list(kc.Surface)[-1] is kc.Surface.HISTORICAL
        assert kc.Surface.HISTORICAL.value == "historical"

    def test_floor_is_aware_utc_on_a_whole_hour(self):
        floor = kc.HISTORICAL_TRADES_FLOOR
        assert floor.tzinfo is UTC
        assert (floor.minute, floor.second, floor.microsecond) == (0, 0, 0)
        assert floor.isoformat() == "2026-01-01T00:00:00+00:00"

    def test_pass_shape(self):
        assert kc.HISTORICAL_PHASE_MINUTES == 30
        assert kc.HISTORICAL_CANDLE_MARKETS_PER_PASS == 1_000
        assert kc.HISTORICAL_SLOW_MARKET_SECONDS == 30

    def test_stop_margin_is_positive(self):
        assert isinstance(kc.HISTORICAL_ARCHIVE_STOP_MARGIN, timedelta)
        assert kc.HISTORICAL_ARCHIVE_STOP_MARGIN > timedelta(0)
        assert kc.HISTORICAL_ARCHIVE_STOP_MARGIN == timedelta(days=1)

    @pytest.mark.parametrize(
        ("budget", "expected_cap"),
        [
            (kc.KALSHI_AUTHENTICATED_RATE_LIMIT, 30_000),
            (kc.KALSHI_PUBLIC_RATE_LIMIT, 9_000),
        ],
    )
    def test_cap_derives_from_the_budget(self, budget: RateLimit, expected_cap: int):
        """Decision 2: cap = requests_per_minute × HISTORICAL_PHASE_MINUTES."""
        assert budget.requests_per_minute * kc.HISTORICAL_PHASE_MINUTES == expected_cap
