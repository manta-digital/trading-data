"""
Integration tests for TradingCalendar against a real TimescaleDB instance.

All tests skip when MT_TIMESCALE_DB_URL is not set.

Run with:
    MT_TIMESCALE_DB_URL=postgresql://... uv run pytest \
        test/integration/test_trading_calendar_integration.py -v
"""

from __future__ import annotations

import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from manta_trading.data.base.adjustment_policy import SessionType
from manta_trading.data.base.trading_calendar import (
    MarketStatus,
    TradingCalendar,
)

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")
skip_no_db = pytest.mark.skipif(
    not TIMESCALE_URL,
    reason="MT_TIMESCALE_DB_URL not set — skipping integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def nyse():
    """Yield a connected TradingCalendar for NYSE; close after test."""
    cal = TradingCalendar("NYSE", TIMESCALE_URL)
    yield cal
    cal.close()


# ---------------------------------------------------------------------------
# Tests — calendar loading
# ---------------------------------------------------------------------------

@skip_no_db
class TestLoadCalendar:

    def test_load_nyse_calendar(self, nyse):
        nyse._ensure_loaded()
        assert nyse.timezone == ZoneInfo("America/New_York")
        assert nyse.market_open_time == time(9, 30)
        assert nyse.market_close_time == time(16, 0)
        assert nyse.has_extended_hours is True

    def test_load_unknown_calendar_raises(self):
        cal = TradingCalendar("UNKNOWN_XYZ", TIMESCALE_URL)
        try:
            with pytest.raises(ValueError, match="not found in database"):
                cal._ensure_loaded()
        finally:
            cal.close()


# ---------------------------------------------------------------------------
# Tests — is_trading_day
# ---------------------------------------------------------------------------

@skip_no_db
class TestIsTradingDay:

    def test_weekday_non_holiday(self, nyse):
        # 2025-01-02 is a Thursday, not a holiday
        assert nyse.is_trading_day(date(2025, 1, 2)) is True

    def test_weekend(self, nyse):
        assert nyse.is_trading_day(date(2025, 1, 4)) is False  # Saturday

    def test_closed_holiday(self, nyse):
        # Christmas 2025 (Thu Dec 25) is a closed holiday in seed data
        assert nyse.is_trading_day(date(2025, 12, 25)) is False

    def test_early_close_is_trading_day(self, nyse):
        # Day after Thanksgiving 2024 (Fri Nov 29) is early_close in seed data
        assert nyse.is_trading_day(date(2024, 11, 29)) is True


# ---------------------------------------------------------------------------
# Tests — get_holidays
# ---------------------------------------------------------------------------

@skip_no_db
class TestGetHolidays:

    def test_holidays_2025(self, nyse):
        holidays = nyse.get_holidays(2025)
        assert len(holidays) > 0
        names = [h.holiday_name for h in holidays]
        assert any("Christmas" in n for n in names)
        # All should have MarketStatus enum values
        for h in holidays:
            assert isinstance(h.market_status, MarketStatus)


# ---------------------------------------------------------------------------
# Tests — get_trading_hours
# ---------------------------------------------------------------------------

@skip_no_db
class TestGetTradingHours:

    def test_normal_day_rth(self, nyse):
        hours = nyse.get_trading_hours(date(2025, 1, 2), SessionType.RTH)
        assert hours is not None
        assert hours.session_start.hour == 9
        assert hours.session_start.minute == 30
        assert hours.session_end.hour == 16
        assert hours.session_end.minute == 0

    def test_early_close_rth(self, nyse):
        # Day after Thanksgiving 2024 — early close at 13:00
        hours = nyse.get_trading_hours(date(2024, 11, 29), SessionType.RTH)
        assert hours is not None
        assert hours.session_end.hour == 13
        assert hours.session_end.minute == 0


# ---------------------------------------------------------------------------
# Tests — get_expected_bar_count
# ---------------------------------------------------------------------------

@skip_no_db
class TestGetExpectedBarCount:

    def test_single_normal_day(self, nyse):
        count = nyse.get_expected_bar_count(date(2025, 1, 2), date(2025, 1, 2))
        assert count == 390  # 6.5h * 60min


# ---------------------------------------------------------------------------
# Tests — SessionClassifier end-to-end
# ---------------------------------------------------------------------------

@skip_no_db
class TestSessionClassifierEndToEnd:

    def test_rth_timestamp_classified_as_rth(self, nyse):
        from manta_trading.data.base.session_classifier import classify_bar_session

        # 2025-01-02 10:00 ET is during RTH
        ts = datetime(2025, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        result = classify_bar_session(ts, nyse)
        assert result == SessionType.RTH

    def test_eth_timestamp_classified_as_eth(self, nyse):
        from manta_trading.data.base.session_classifier import classify_bar_session

        # 2025-01-02 07:00 ET is pre-market (ETH)
        ts = datetime(2025, 1, 2, 7, 0, tzinfo=ZoneInfo("America/New_York"))
        result = classify_bar_session(ts, nyse)
        assert result == SessionType.ETH
