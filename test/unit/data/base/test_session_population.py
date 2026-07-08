"""Unit tests for populate_trading_sessions pure function.

Parity assertion: populate_trading_sessions output must match
TradingCalendar._build_trading_hours for a sample of NYSE dates.
No DB connection is used in any test.
"""

from __future__ import annotations

from datetime import date, time
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from manta_trading.data.base.adjustment_policy import SessionType
from manta_trading.data.base.session_population import populate_trading_sessions
from manta_trading.data.base.trading_calendar import TradingCalendar

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

_CALENDARS_ROW = {
    "timezone": "America/New_York",
    "market_open": time(9, 30),
    "market_close": time(16, 0),
}

# Holidays fixture: covers every scenario required by T4.
# Format matches what migration 026 will pass (holiday_date as date object).
_HOLIDAYS_ROWS = [
    # Christmas 2026 — closed
    {
        "holiday_date": date(2026, 12, 25),
        "market_status": "closed",
        "early_close_time": None,
        "late_open_time": None,
    },
    # Black Friday 2026 — early close at 13:00 ET
    {
        "holiday_date": date(2026, 11, 27),
        "market_status": "early_close",
        "early_close_time": time(13, 0),
        "late_open_time": None,
    },
    # Synthetic late-open day (no real 2026 example; use Jan 2 as synthetic)
    {
        "holiday_date": date(2026, 1, 2),
        "market_status": "late_open",
        "early_close_time": None,
        "late_open_time": time(11, 0),
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_calendar_no_pool() -> TradingCalendar:
    """Pre-loaded TradingCalendar with a no-op pool (no DB)."""
    pool = MagicMock()
    with patch("manta_trading.data.base.trading_calendar.ConnectionPool", return_value=pool):
        cal = TradingCalendar("NYSE", "postgresql://localhost/test")
    cal.calendar_name = "New York Stock Exchange"
    cal.timezone = _ET
    cal.market_open_time = time(9, 30)
    cal.market_close_time = time(16, 0)
    cal.has_extended_hours = True
    cal.extended_open_time = time(4, 0)
    cal.extended_close_time = time(20, 0)
    cal._loaded = True
    return cal


def _parity_assert(row: dict, cal: TradingCalendar, d: date, early_close: time | None, late_open: time | None) -> None:
    """Assert a populate_trading_sessions row matches _build_trading_hours output."""
    expected = cal._build_trading_hours(d, SessionType.RTH, early_close, late_open)
    assert expected is not None, f"Expected _build_trading_hours to return a result for {d}"
    # Row stores UTC; _build_trading_hours stores local tz — compare as instants.
    assert row["session_open_utc"] == expected.session_start.astimezone(_UTC), (
        f"open mismatch for {d}"
    )
    assert row["session_close_utc"] == expected.session_end.astimezone(_UTC), (
        f"close mismatch for {d}"
    )


# ---------------------------------------------------------------------------
# Tests: basic row generation
# ---------------------------------------------------------------------------

class TestPopulateTradingSessions:

    def test_normal_weekday_produces_row(self):
        rows = populate_trading_sessions(
            "NYSE", date(2026, 1, 5), date(2026, 1, 5), _CALENDARS_ROW, []
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["calendar_id"] == "NYSE"
        assert row["session_date"] == date(2026, 1, 5)

    def test_saturday_produces_no_row(self):
        rows = populate_trading_sessions(
            "NYSE", date(2026, 1, 3), date(2026, 1, 3), _CALENDARS_ROW, []
        )
        assert rows == []

    def test_sunday_produces_no_row(self):
        rows = populate_trading_sessions(
            "NYSE", date(2026, 1, 4), date(2026, 1, 4), _CALENDARS_ROW, []
        )
        assert rows == []

    def test_closed_holiday_produces_no_row(self):
        rows = populate_trading_sessions(
            "NYSE", date(2026, 12, 25), date(2026, 12, 25), _CALENDARS_ROW, _HOLIDAYS_ROWS
        )
        assert rows == []

    def test_early_close_produces_row_with_override(self):
        rows = populate_trading_sessions(
            "NYSE", date(2026, 11, 27), date(2026, 11, 27), _CALENDARS_ROW, _HOLIDAYS_ROWS
        )
        assert len(rows) == 1
        row = rows[0]
        # Early close at 13:00 ET; UTC = 18:00 UTC (EST = UTC-5)
        assert row["session_close_utc"].hour == 18
        assert row["session_close_utc"].minute == 0
        # Open unchanged at 09:30 ET = 14:30 UTC
        assert row["session_open_utc"].hour == 14
        assert row["session_open_utc"].minute == 30

    def test_late_open_produces_row_with_override(self):
        rows = populate_trading_sessions(
            "NYSE", date(2026, 1, 2), date(2026, 1, 2), _CALENDARS_ROW, _HOLIDAYS_ROWS
        )
        assert len(rows) == 1
        row = rows[0]
        # Late open at 11:00 ET; in January (EST = UTC-5) = 16:00 UTC
        assert row["session_open_utc"].hour == 16
        # Close unchanged at 16:00 ET = 21:00 UTC
        assert row["session_close_utc"].hour == 21

    def test_weekend_dates_skipped_in_range(self):
        # Mon Jan 5 through Sun Jan 11 — 5 trading days
        rows = populate_trading_sessions(
            "NYSE", date(2026, 1, 5), date(2026, 1, 11), _CALENDARS_ROW, []
        )
        assert len(rows) == 5
        for row in rows:
            assert row["session_date"].weekday() < 5

    def test_row_keys_present(self):
        rows = populate_trading_sessions(
            "NYSE", date(2026, 1, 5), date(2026, 1, 5), _CALENDARS_ROW, []
        )
        row = rows[0]
        assert set(row.keys()) == {
            "calendar_id", "session_date", "session_open_utc", "session_close_utc"
        }

    def test_timestamps_are_utc(self):
        rows = populate_trading_sessions(
            "NYSE", date(2026, 1, 5), date(2026, 1, 5), _CALENDARS_ROW, []
        )
        row = rows[0]
        # UTC offset must be zero
        assert row["session_open_utc"].utcoffset().total_seconds() == 0
        assert row["session_close_utc"].utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# Tests: DST transitions
# ---------------------------------------------------------------------------

class TestDSTHandling:

    def test_dst_spring_forward_2026(self):
        """2026-03-08 is DST spring-forward Sunday; Monday 3/9 uses EDT (UTC-4)."""
        rows = populate_trading_sessions(
            "NYSE", date(2026, 3, 9), date(2026, 3, 9), _CALENDARS_ROW, []
        )
        assert len(rows) == 1
        row = rows[0]
        # 09:30 EDT = UTC-4 → 13:30 UTC
        assert row["session_open_utc"].hour == 13
        assert row["session_open_utc"].minute == 30
        # 16:00 EDT = 20:00 UTC
        assert row["session_close_utc"].hour == 20

    def test_dst_fall_back_2026(self):
        """2026-11-01 is DST fall-back Sunday; Monday 11/2 uses EST (UTC-5)."""
        rows = populate_trading_sessions(
            "NYSE", date(2026, 11, 2), date(2026, 11, 2), _CALENDARS_ROW, []
        )
        assert len(rows) == 1
        row = rows[0]
        # 09:30 EST = UTC-5 → 14:30 UTC
        assert row["session_open_utc"].hour == 14
        assert row["session_open_utc"].minute == 30
        # 16:00 EST = 21:00 UTC
        assert row["session_close_utc"].hour == 21


# ---------------------------------------------------------------------------
# Tests: parity with TradingCalendar._build_trading_hours (keystone — SC#9)
# ---------------------------------------------------------------------------

class TestParityWithBuildTradingHours:
    """Parity between populate_trading_sessions and _build_trading_hours.

    This is the architectural keystone (slice design success criterion #9).
    Any algorithm drift between the two paths shows up here.
    """

    @pytest.fixture()
    def cal(self) -> TradingCalendar:
        return _make_calendar_no_pool()

    @pytest.mark.parametrize("d,early_close,late_open", [
        (date(2026, 1, 5), None, None),          # normal Monday
        (date(2026, 3, 9), None, None),           # DST spring-forward week
        (date(2026, 11, 2), None, None),          # DST fall-back week
        (date(2026, 11, 27), time(13, 0), None),  # Black Friday early close
        (date(2026, 1, 2), None, time(11, 0)),    # late-open synthetic
    ])
    def test_parity(
        self,
        cal: TradingCalendar,
        d: date,
        early_close: time | None,
        late_open: time | None,
    ) -> None:
        holidays: list[dict] = []
        if early_close is not None or late_open is not None:
            holidays = [
                {
                    "holiday_date": d,
                    "market_status": "early_close" if early_close else "late_open",
                    "early_close_time": early_close,
                    "late_open_time": late_open,
                }
            ]
        rows = populate_trading_sessions(
            "NYSE", d, d, _CALENDARS_ROW, holidays
        )
        assert len(rows) == 1, f"Expected one row for {d}"
        _parity_assert(rows[0], cal, d, early_close, late_open)

    def test_closed_holiday_consistent(self, cal: TradingCalendar) -> None:
        """Closed holiday: populate returns no row; _build_trading_hours would
        never be called for closed dates (is_trading_day gates it)."""
        rows = populate_trading_sessions(
            "NYSE",
            date(2026, 12, 25),
            date(2026, 12, 25),
            _CALENDARS_ROW,
            _HOLIDAYS_ROWS,
        )
        assert rows == []

    def test_weekend_consistent(self, cal: TradingCalendar) -> None:
        """Weekend: populate returns no row; _build_trading_hours is never called."""
        rows = populate_trading_sessions(
            "NYSE", date(2026, 1, 3), date(2026, 1, 3), _CALENDARS_ROW, []
        )
        assert rows == []
