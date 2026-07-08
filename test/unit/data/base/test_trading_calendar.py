"""
Unit tests for trading_calendar module.

Tests MarketStatus StrEnum, Holiday/TradingHours dataclasses,
and all TradingCalendar methods with mocked DB connections.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from manta_trading.data.base.adjustment_policy import SessionType
from manta_trading.data.base.trading_calendar import (
    Holiday,
    MarketStatus,
    TradingCalendar,
    TradingHours,
)

# ---------------------------------------------------------------------------
# Constants for test data
# ---------------------------------------------------------------------------

_ET = ZoneInfo("America/New_York")

_CALENDAR_ROW = {
    "calendar_id": "NYSE",
    "calendar_name": "New York Stock Exchange",
    "timezone": "America/New_York",
    "market_open_time": time(9, 30),
    "market_close_time": time(16, 0),
    "has_extended_hours": True,
    "extended_open_time": time(4, 0),
    "extended_close_time": time(20, 0),
}

_NO_ETH_CALENDAR_ROW = {
    **_CALENDAR_ROW,
    "has_extended_hours": False,
    "extended_open_time": None,
    "extended_close_time": None,
}


# ---------------------------------------------------------------------------
# Helpers — mock wiring
# ---------------------------------------------------------------------------

def _make_cursor(fetchone=None, fetchall=None):
    """Create a mock cursor with configurable fetch results."""
    cur = MagicMock()
    cur.execute = MagicMock()
    cur.fetchone = MagicMock(return_value=fetchone)
    cur.fetchall = MagicMock(return_value=fetchall if fetchall is not None else [])
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    return cur


def _make_conn(cursor):
    """Create a mock connection wrapping a cursor."""
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cursor)
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def _make_pool(cursor):
    """Create a mock pool wrapping a connection wrapping a cursor."""
    conn = _make_conn(cursor)
    pool = MagicMock()
    pool_conn_ctx = MagicMock()
    pool_conn_ctx.__enter__ = MagicMock(return_value=conn)
    pool_conn_ctx.__exit__ = MagicMock(return_value=False)
    pool.connection = MagicMock(return_value=pool_conn_ctx)
    return pool


def _make_calendar(calendar_row=None, cursor_fetchone=None, cursor_fetchall=None):
    """
    Create a TradingCalendar with a mocked pool.

    If calendar_row is provided, the calendar is pre-loaded (skips _ensure_loaded DB call).
    Otherwise cursor_fetchone/fetchall control what the mock DB returns.
    """
    cur = _make_cursor(fetchone=cursor_fetchone, fetchall=cursor_fetchall)
    pool = _make_pool(cur)

    with patch("manta_trading.data.base.trading_calendar.ConnectionPool", return_value=pool):
        cal = TradingCalendar("NYSE", "postgresql://localhost/test")

    if calendar_row is not None:
        # Pre-load to avoid needing mock for _ensure_loaded
        cal.calendar_name = calendar_row["calendar_name"]
        cal.timezone = ZoneInfo(calendar_row["timezone"])
        cal.market_open_time = calendar_row["market_open_time"]
        cal.market_close_time = calendar_row["market_close_time"]
        cal.has_extended_hours = calendar_row["has_extended_hours"]
        cal.extended_open_time = calendar_row["extended_open_time"]
        cal.extended_close_time = calendar_row["extended_close_time"]
        cal._loaded = True

    return cal, pool, cur


# ===================================================================
# MarketStatus StrEnum
# ===================================================================

class TestMarketStatus:

    def test_members(self):
        assert set(MarketStatus) == {
            MarketStatus.CLOSED,
            MarketStatus.EARLY_CLOSE,
            MarketStatus.LATE_OPEN,
        }

    def test_values_match_db(self):
        assert MarketStatus.CLOSED.value == "closed"
        assert MarketStatus.EARLY_CLOSE.value == "early_close"
        assert MarketStatus.LATE_OPEN.value == "late_open"

    def test_string_comparison(self):
        assert MarketStatus.CLOSED == "closed"
        assert MarketStatus.EARLY_CLOSE == "early_close"
        assert MarketStatus.LATE_OPEN == "late_open"

    def test_construct_from_value(self):
        assert MarketStatus("closed") is MarketStatus.CLOSED
        assert MarketStatus("early_close") is MarketStatus.EARLY_CLOSE


# ===================================================================
# Holiday dataclass
# ===================================================================

class TestHoliday:

    def test_instantiation_with_enum(self):
        h = Holiday(
            holiday_date=date(2024, 12, 25),
            holiday_name="Christmas",
            market_status=MarketStatus.CLOSED,
        )
        assert h.market_status is MarketStatus.CLOSED
        assert h.early_close_time is None

    def test_early_close(self):
        h = Holiday(
            holiday_date=date(2024, 11, 29),
            holiday_name="Day after Thanksgiving",
            market_status=MarketStatus.EARLY_CLOSE,
            early_close_time=time(13, 0),
        )
        assert h.early_close_time == time(13, 0)
        assert h.market_status is MarketStatus.EARLY_CLOSE


# ===================================================================
# TradingHours dataclass
# ===================================================================

class TestTradingHours:

    def test_instantiation(self):
        start = datetime(2024, 1, 2, 9, 30, tzinfo=_ET)
        end = datetime(2024, 1, 2, 16, 0, tzinfo=_ET)
        hours = TradingHours(
            session_start=start,
            session_end=end,
            session_type=SessionType.RTH,
            is_trading_day=True,
        )
        assert hours.session_start == start
        assert hours.session_end == end
        assert hours.session_type == SessionType.RTH


# ===================================================================
# TradingCalendar — core (Task 4)
# ===================================================================

class TestTradingCalendarCore:

    def test_init_does_not_query_db(self):
        """Constructor creates pool but does not call connection()."""
        cal, pool, _cur = _make_calendar()
        assert cal.calendar_id == "NYSE"
        assert cal._loaded is False
        pool.connection.assert_not_called()

    def test_ensure_loaded_queries_db(self):
        cal, pool, cur = _make_calendar(cursor_fetchone=_CALENDAR_ROW)
        cal._ensure_loaded()
        assert cal._loaded is True
        assert cal.timezone == _ET
        assert cal.market_open_time == time(9, 30)
        pool.connection.assert_called_once()

    def test_ensure_loaded_idempotent(self):
        cal, pool, cur = _make_calendar(cursor_fetchone=_CALENDAR_ROW)
        cal._ensure_loaded()
        cal._ensure_loaded()
        # Pool.connection called only once
        assert pool.connection.call_count == 1

    def test_ensure_loaded_raises_on_missing(self):
        cal, _pool, _cur = _make_calendar(cursor_fetchone=None)
        with pytest.raises(ValueError, match="not found in database"):
            cal._ensure_loaded()

    def test_invalidate_cache(self):
        cal, _pool, _cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cal._cache["foo"] = "bar"
        cal._invalidate_cache()
        assert cal._cache == {}

    def test_close(self):
        cal, pool, _cur = _make_calendar()
        cal.close()
        pool.close.assert_called_once()


# ===================================================================
# is_trading_day — slice 144 (reads from trading_sessions)
# ===================================================================

# Far-future horizon for tests that should not trigger OutOfHorizonError.
_FAR_HORIZON = date(2099, 12, 31)


def _trading_sessions_fetchone_seq(
    *,
    horizon_end: date | None = _FAR_HORIZON,
    is_trading: bool,
    horizon_already_cached: bool = False,
):
    """Build the fetchone side_effect sequence for an is_trading_day call.

    When horizon_already_cached=False, _get_horizon_end runs:
      1. to_regclass('trading_sessions')  → ('trading_sessions',) or (None,)
      2. SELECT MAX(session_date)        → (horizon_end,)
    Then is_trading_day's EXISTS query:
      3. SELECT EXISTS(...)               → (is_trading,)
    """
    seq = []
    if not horizon_already_cached:
        if horizon_end is None:
            seq.append((None,))  # to_regclass returns NULL → no horizon check
        else:
            seq.append(("trading_sessions",))
            seq.append((horizon_end,))
    seq.append((is_trading,))
    return seq


class TestIsTradingDay:
    """is_trading_day reads from trading_sessions (slice 144)."""

    def test_normal_weekday_returns_true(self):
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _trading_sessions_fetchone_seq(is_trading=True)
        assert cal.is_trading_day(date(2024, 1, 2)) is True

    def test_weekend_returns_false(self):
        """Trading_sessions has no weekend rows → EXISTS returns false."""
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _trading_sessions_fetchone_seq(is_trading=False)
        assert cal.is_trading_day(date(2024, 1, 6)) is False  # Saturday

    def test_closed_holiday_returns_false(self):
        """Closed holidays are absent from trading_sessions → EXISTS returns false."""
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _trading_sessions_fetchone_seq(is_trading=False)
        assert cal.is_trading_day(date(2024, 12, 25)) is False

    def test_early_close_is_trading_day(self):
        """Early-close days are present in trading_sessions → EXISTS returns true."""
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _trading_sessions_fetchone_seq(is_trading=True)
        assert cal.is_trading_day(date(2024, 11, 29)) is True

    def test_out_of_horizon_raises(self):
        """Date past horizon_end raises OutOfHorizonError."""
        from manta_trading.data.base.trading_calendar import OutOfHorizonError
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        # Set a near horizon
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (date(2026, 12, 31),),  # horizon end
        ]
        with pytest.raises(OutOfHorizonError) as exc_info:
            cal.is_trading_day(date(2030, 6, 15))
        assert exc_info.value.calendar_id == "NYSE"
        assert exc_info.value.date == date(2030, 6, 15)
        assert exc_info.value.horizon_end == date(2026, 12, 31)

    def test_caching(self):
        cal, pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        # First call: 3 fetchones (regclass, max, exists). Second: cache hit.
        cur.fetchone.side_effect = _trading_sessions_fetchone_seq(is_trading=True)
        cal.is_trading_day(date(2024, 1, 2))
        cal.is_trading_day(date(2024, 1, 2))
        # First call: 2 connections (horizon + EXISTS). Second: 0 (cache).
        assert pool.connection.call_count == 2

    def test_calls_ensure_loaded(self):
        """is_trading_day triggers _ensure_loaded on first call."""
        cal, _pool, cur = _make_calendar(cursor_fetchone=_CALENDAR_ROW)
        # _ensure_loaded fetchone returns calendar row; then horizon + EXISTS.
        cur.fetchone.side_effect = [
            _CALENDAR_ROW,         # _ensure_loaded
            ("trading_sessions",), # _get_horizon_end: to_regclass
            (_FAR_HORIZON,),       # _get_horizon_end: MAX(session_date)
            (True,),               # EXISTS
        ]
        assert cal.is_trading_day(date(2024, 1, 2)) is True
        assert cal._loaded is True


# ===================================================================
# get_holidays (Task 6)
# ===================================================================

class TestGetHolidays:

    def test_returns_holidays_list(self):
        rows = [
            {
                "holiday_date": date(2024, 12, 25),
                "holiday_name": "Christmas",
                "market_status": "closed",
                "early_close_time": None,
                "late_open_time": None,
            },
            {
                "holiday_date": date(2024, 11, 29),
                "holiday_name": "Day after Thanksgiving",
                "market_status": "early_close",
                "early_close_time": time(13, 0),
                "late_open_time": None,
            },
        ]
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchall.return_value = rows

        holidays = cal.get_holidays(2024)
        assert len(holidays) == 2
        assert holidays[0].market_status is MarketStatus.CLOSED
        assert holidays[1].market_status is MarketStatus.EARLY_CLOSE
        assert holidays[1].early_close_time == time(13, 0)

    def test_empty_year(self):
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchall.return_value = []
        assert cal.get_holidays(2030) == []

    def test_caching(self):
        cal, pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchall.return_value = []
        cal.get_holidays(2024)
        cal.get_holidays(2024)
        assert pool.connection.call_count == 1

    def test_calls_ensure_loaded(self):
        cal, _pool, cur = _make_calendar(cursor_fetchone=_CALENDAR_ROW)
        cur.fetchone.side_effect = [_CALENDAR_ROW]
        cur.fetchall.return_value = []
        cal.get_holidays(2024)
        assert cal._loaded is True


# ===================================================================
# get_trading_hours — slice 144 (RTH reads from trading_sessions)
# ===================================================================

def _ts_session_row(d: date, open_h: int = 14, open_m: int = 30,
                    close_h: int = 21, close_m: int = 0) -> dict:
    """Build a trading_sessions row dict (UTC timestamps).

    Defaults: 14:30 / 21:00 UTC = 09:30 / 16:00 EST (NYSE standard).
    """
    from datetime import timezone
    return {
        "session_open_utc": datetime(d.year, d.month, d.day, open_h, open_m, tzinfo=timezone.utc),
        "session_close_utc": datetime(d.year, d.month, d.day, close_h, close_m, tzinfo=timezone.utc),
    }


class TestGetTradingHours:
    """RTH path reads from trading_sessions; ETH/ALL fallback through _build_trading_hours."""

    def test_rth_normal_day(self):
        d = date(2024, 1, 2)
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),    # to_regclass
            (_FAR_HORIZON,),          # MAX(session_date)
            _ts_session_row(d),       # session row (dict_row)
        ]
        hours = cal.get_trading_hours(d, SessionType.RTH)
        assert hours is not None
        assert hours.session_start.hour == 9
        assert hours.session_start.minute == 30
        assert hours.session_end.hour == 16
        assert hours.session_end.minute == 0
        assert hours.session_type == SessionType.RTH

    def test_rth_early_close(self):
        """Black Friday 2024 — close 13:00 ET = 18:00 UTC."""
        d = date(2024, 11, 29)
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            _ts_session_row(d, close_h=18, close_m=0),
        ]
        hours = cal.get_trading_hours(d, SessionType.RTH)
        assert hours.session_end.hour == 13

    def test_rth_late_open(self):
        """Late-open 11:00 ET = 16:00 UTC."""
        d = date(2024, 1, 2)
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            _ts_session_row(d, open_h=16, open_m=0),
        ]
        hours = cal.get_trading_hours(d, SessionType.RTH)
        assert hours.session_start.hour == 11

    def test_rth_weekend_returns_none(self):
        """Saturday absent from trading_sessions → fetchone returns None."""
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            None,  # session row missing
        ]
        assert cal.get_trading_hours(date(2024, 1, 6), SessionType.RTH) is None

    def test_rth_closed_holiday_returns_none(self):
        """Christmas absent from trading_sessions → returns None."""
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            None,
        ]
        assert cal.get_trading_hours(date(2024, 12, 25), SessionType.RTH) is None

    def test_rth_out_of_horizon_raises(self):
        from manta_trading.data.base.trading_calendar import OutOfHorizonError
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (date(2026, 12, 31),),
        ]
        with pytest.raises(OutOfHorizonError):
            cal.get_trading_hours(date(2030, 6, 15), SessionType.RTH)

    def test_eth_with_extended_hours(self):
        """ETH path: is_trading_day → trading_holidays for overrides → _build_trading_hours."""
        d = date(2024, 1, 2)
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        # is_trading_day: regclass, max, EXISTS=true; then trading_holidays fetchone=None
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            (True,),
            None,  # no holiday override
        ]
        hours = cal.get_trading_hours(d, SessionType.ETH)
        assert hours is not None
        assert hours.session_start.hour == 4
        assert hours.session_end.hour == 20

    def test_eth_without_extended_hours(self):
        d = date(2024, 1, 2)
        cal, _pool, cur = _make_calendar(calendar_row=_NO_ETH_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            (True,),
            None,
        ]
        assert cal.get_trading_hours(d, SessionType.ETH) is None

    def test_all_session_type(self):
        """ALL session: min(ETH_open, RTH_open), max(ETH_close, RTH_close)."""
        d = date(2024, 1, 2)
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            (True,),
            None,
        ]
        hours = cal.get_trading_hours(d, SessionType.ALL)
        assert hours is not None
        assert hours.session_start.hour == 4
        assert hours.session_end.hour == 20

    def test_caching(self):
        d = date(2024, 1, 2)
        cal, pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            _ts_session_row(d),
        ]
        cal.get_trading_hours(d, SessionType.RTH)
        cal.get_trading_hours(d, SessionType.RTH)
        # First call: horizon (1) + RTH query (1) = 2 connections.
        # Second call: cache hit, 0 connections.
        assert pool.connection.call_count == 2

    def test_datetimes_are_timezone_aware(self):
        d = date(2024, 1, 2)
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = [
            ("trading_sessions",),
            (_FAR_HORIZON,),
            _ts_session_row(d),
        ]
        hours = cal.get_trading_hours(d, SessionType.RTH)
        assert hours.session_start.tzinfo is not None
        assert hours.session_end.tzinfo is not None


# ===================================================================
# get_expected_bar_count — slice 144
# ===================================================================

def _bar_count_fetchone_seq(
    *,
    dates: list[date],
    is_trading: dict[date, bool],
    open_close_utc: dict[date, tuple[int, int, int, int]] | None = None,
    horizon_end: date = _FAR_HORIZON,
) -> list:
    """Build the fetchone sequence for a get_expected_bar_count loop.

    For RTH: each date in `dates` produces (session_row | None). The first
    call additionally produces (regclass, horizon).

    open_close_utc maps date → (open_h, open_m, close_h, close_m) UTC times.
    """
    if open_close_utc is None:
        open_close_utc = {}

    seq = [
        ("trading_sessions",),
        (horizon_end,),
    ]
    for d in dates:
        if is_trading.get(d, True):
            oh, om, ch, cm = open_close_utc.get(d, (14, 30, 21, 0))
            seq.append({
                "session_open_utc": datetime(d.year, d.month, d.day, oh, om,
                                              tzinfo=__import__("datetime").timezone.utc),
                "session_close_utc": datetime(d.year, d.month, d.day, ch, cm,
                                               tzinfo=__import__("datetime").timezone.utc),
            })
        else:
            seq.append(None)
    return seq


class TestGetExpectedBarCount:

    def test_single_normal_day(self):
        d = date(2024, 1, 2)  # Tuesday
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _bar_count_fetchone_seq(
            dates=[d], is_trading={d: True}
        )
        count = cal.get_expected_bar_count(d, d)
        # RTH: 09:30-16:00 ET = 390 min
        assert count == 390

    def test_single_day_5min(self):
        d = date(2024, 1, 2)
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _bar_count_fetchone_seq(
            dates=[d], is_trading={d: True}
        )
        count = cal.get_expected_bar_count(d, d, timeframe_minutes=5)
        assert count == 78  # 390 / 5

    def test_weekend_contributes_zero(self):
        # Thu Jan 4 + Fri Jan 5 + Sat Jan 6 + Sun Jan 7 = 2 trading days
        dates_list = [date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 6), date(2024, 1, 7)]
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _bar_count_fetchone_seq(
            dates=dates_list,
            is_trading={
                date(2024, 1, 4): True,
                date(2024, 1, 5): True,
                date(2024, 1, 6): False,
                date(2024, 1, 7): False,
            },
        )
        count = cal.get_expected_bar_count(date(2024, 1, 4), date(2024, 1, 7))
        assert count == 780  # 2 * 390

    def test_closed_holiday_contributes_zero(self):
        # Dec 24 (Tue) + Dec 25 (Wed, closed) + Dec 26 (Thu)
        dates_list = [date(2024, 12, 24), date(2024, 12, 25), date(2024, 12, 26)]
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _bar_count_fetchone_seq(
            dates=dates_list,
            is_trading={
                date(2024, 12, 24): True,
                date(2024, 12, 25): False,
                date(2024, 12, 26): True,
            },
        )
        count = cal.get_expected_bar_count(date(2024, 12, 24), date(2024, 12, 26))
        # 2 normal days * 390 = 780
        assert count == 780

    def test_empty_range(self):
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        # No iterations expected; no DB calls
        count = cal.get_expected_bar_count(date(2024, 1, 5), date(2024, 1, 2))
        assert count == 0

    def test_dst_transition_day(self):
        """DST day: 09:30-16:00 ET = 390 bars (UTC values shift but interval is same)."""
        # Mon 2024-03-11 is post DST spring-forward; 09:30 EDT = 13:30 UTC
        d = date(2024, 3, 11)
        cal, _pool, cur = _make_calendar(calendar_row=_CALENDAR_ROW)
        cur.fetchone.side_effect = _bar_count_fetchone_seq(
            dates=[d],
            is_trading={d: True},
            open_close_utc={d: (13, 30, 20, 0)},  # EDT offsets
        )
        count = cal.get_expected_bar_count(d, d)
        assert count == 390
