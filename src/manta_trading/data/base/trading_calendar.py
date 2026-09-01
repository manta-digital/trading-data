"""
Trading Calendar module for exchange-specific calendar logic.

This module provides the TradingCalendar class which manages trading schedules,
holidays, and session hours for different exchanges. It supports RTH (Regular
Trading Hours) and ETH (Extended Trading Hours) classifications, DST handling,
and expected bar count calculations.

Backed by psycopg3 ConnectionPool against trading_calendars / trading_holidays
tables (slice 102). Uses per-instance dict cache (not lru_cache).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from manta_trading.data.base.adjustment_policy import SessionType
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


class MarketStatus(StrEnum):
    """Market status for holidays. Values match DB column values exactly."""

    CLOSED = "closed"
    EARLY_CLOSE = "early_close"
    LATE_OPEN = "late_open"


@dataclass
class Holiday:
    """
    Represents a market holiday or early close day.

    Attributes:
        holiday_date: Date of the holiday
        holiday_name: Name of the holiday
        market_status: Status ('closed', 'early_close', 'late_open')
        early_close_time: Time of early close (if applicable)
        late_open_time: Time of late open (if applicable)
    """

    holiday_date: date
    holiday_name: str
    market_status: MarketStatus
    early_close_time: time | None = None
    late_open_time: time | None = None


@dataclass
class TradingHours:
    """
    Represents trading hours for a specific date and session type.

    Attributes:
        session_start: Start time of trading session
        session_end: End time of trading session
        session_type: Type of session (RTH, ETH, ALL)
        is_trading_day: Whether this is a trading day
    """

    session_start: datetime
    session_end: datetime
    session_type: SessionType
    is_trading_day: bool


class OutOfHorizonError(Exception):
    """Raised when a date falls outside the populated trading_sessions horizon.

    Run ``mt data --extend`` to extend the horizon before retrying.
    """

    def __init__(self, calendar_id: str, query_date: date, horizon_end: date) -> None:
        self.calendar_id = calendar_id
        self.date = query_date
        self.horizon_end = horizon_end
        super().__init__(
            f"Date {query_date} is beyond the populated trading_sessions horizon "
            f"for calendar '{calendar_id}' (horizon ends {horizon_end}). "
            "Run 'mt data extend' to extend the horizon."
        )


class TradingCalendar:
    """
    Manages trading calendar logic for a specific exchange.

    Backed by psycopg3 ConnectionPool against trading_calendars and
    trading_holidays tables. Uses per-instance dict cache to avoid
    cross-instance pollution. Lazy-loads calendar metadata on first use.
    """

    def __init__(self, calendar_id: str, conninfo: str) -> None:
        self.calendar_id = calendar_id
        self._pool = ConnectionPool(conninfo, min_size=1, max_size=3, open=True)
        self._loaded = False
        self._cache: dict[str, Any] = {}

        # Set by _ensure_loaded()
        self.calendar_name: str | None = None
        self.timezone: ZoneInfo | None = None
        self.market_open_time: time | None = None
        self.market_close_time: time | None = None
        self.has_extended_hours: bool = False
        self.extended_open_time: time | None = None
        self.extended_close_time: time | None = None

        _logger.info("TradingCalendar initialized for %s", calendar_id)

    def close(self) -> None:
        """Close the connection pool."""
        self._pool.close()

    def _ensure_loaded(self) -> None:
        """Lazy-load calendar metadata from DB on first use."""
        if self._loaded:
            return

        # Schema column names (per migration 004_trading_calendars):
        # exchange_name, market_open, market_close, extended_open,
        # extended_close. Aliased to the historical attribute names this
        # class exposes to keep the rest of the file (and any external
        # callers) untouched.
        sql = (
            "SELECT calendar_id,"
            "  exchange_name AS calendar_name,"
            "  timezone,"
            "  market_open  AS market_open_time,"
            "  market_close AS market_close_time,"
            "  has_extended_hours,"
            "  extended_open  AS extended_open_time,"
            "  extended_close AS extended_close_time "
            "FROM trading_calendars WHERE calendar_id = %s"
        )
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (self.calendar_id,))
                row = cur.fetchone()

        if row is None:
            raise ValueError(
                f"Trading calendar '{self.calendar_id}' not found in database"
            )

        self.calendar_name = row["calendar_name"]
        self.timezone = ZoneInfo(row["timezone"])
        self.market_open_time = row["market_open_time"]
        self.market_close_time = row["market_close_time"]
        self.has_extended_hours = row["has_extended_hours"]
        self.extended_open_time = row["extended_open_time"]
        self.extended_close_time = row["extended_close_time"]
        self._loaded = True

    def _invalidate_cache(self) -> None:
        """Clear the per-instance cache."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def _get_horizon_end(self) -> date | None:
        """Return MAX(session_date) for this calendar; None if table unpopulated."""
        key = "horizon_end"
        if key in self._cache:
            return self._cache[key]

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT to_regclass('trading_sessions')",
                )
                ts_row = cur.fetchone()
                if ts_row is None or ts_row[0] is None:
                    self._cache[key] = None
                    return None
                cur.execute(
                    "SELECT MAX(session_date) FROM trading_sessions "
                    "WHERE calendar_id = %s",
                    (self.calendar_id,),
                )
                row = cur.fetchone()

        horizon = row[0] if row else None
        self._cache[key] = horizon
        return horizon

    def is_trading_day(self, check_date: date) -> bool:
        """Check if a given date is a trading day.

        Returns False for weekends and full-closure holidays.
        Early-close days ARE trading days.

        Raises:
            OutOfHorizonError: if check_date is beyond the populated
                trading_sessions horizon. Run ``mt data --extend``.
        """
        self._ensure_loaded()

        key = f"is_trading_day:{check_date}"
        if key in self._cache:
            return self._cache[key]

        horizon = self._get_horizon_end()
        if horizon is not None and check_date > horizon:
            raise OutOfHorizonError(self.calendar_id, check_date, horizon)

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS("
                    "  SELECT 1 FROM trading_sessions "
                    "  WHERE calendar_id = %s AND session_date = %s"
                    ")",
                    (self.calendar_id, check_date),
                )
                row = cur.fetchone()

        result = bool(row and row[0])
        self._cache[key] = result
        return result

    def get_holidays(self, year: int) -> list[Holiday]:
        """Get all holidays for a specific year."""
        self._ensure_loaded()

        key = f"holidays:{year}"
        if key in self._cache:
            return self._cache[key]

        sql = (
            "SELECT holiday_date, holiday_name, market_status,"
            "  early_close_time, late_open_time "
            "FROM trading_holidays "
            "WHERE calendar_id = %s AND EXTRACT(YEAR FROM holiday_date) = %s "
            "ORDER BY holiday_date"
        )
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (self.calendar_id, year))
                rows = cur.fetchall()

        holidays = [
            Holiday(
                holiday_date=row["holiday_date"],
                holiday_name=row["holiday_name"],
                market_status=MarketStatus(row["market_status"]),
                early_close_time=row["early_close_time"],
                late_open_time=row["late_open_time"],
            )
            for row in rows
        ]

        self._cache[key] = holidays
        return holidays

    def get_trading_hours(
        self,
        trade_date: date,
        session_type: SessionType = SessionType.RTH,
    ) -> TradingHours | None:
        """Get trading hours for a specific date and session type.

        Returns None if the date is not a trading day (for RTH),
        or if the exchange has no extended hours (for ETH).

        Raises:
            OutOfHorizonError: if trade_date is beyond the populated
                trading_sessions horizon (RTH path only).
        """
        self._ensure_loaded()

        key = f"trading_hours:{trade_date}:{session_type.value}"
        if key in self._cache:
            return self._cache[key]

        if session_type == SessionType.RTH:
            # Read directly from trading_sessions; absence = non-trading day.
            horizon = self._get_horizon_end()
            if horizon is not None and trade_date > horizon:
                raise OutOfHorizonError(self.calendar_id, trade_date, horizon)

            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT session_open_utc, session_close_utc "
                        "FROM trading_sessions "
                        "WHERE calendar_id = %s AND session_date = %s",
                        (self.calendar_id, trade_date),
                    )
                    row = cur.fetchone()

            if row is None:
                self._cache[key] = None
                return None

            tz = self.timezone
            result: TradingHours | None = TradingHours(
                session_start=row["session_open_utc"].astimezone(tz),
                session_end=row["session_close_utc"].astimezone(tz),
                session_type=session_type,
                is_trading_day=True,
            )
            self._cache[key] = result
            return result

        # ETH / ALL: trading_sessions doesn't store extended hours.
        # Use is_trading_day to gate, then build from calendar metadata.
        if not self.is_trading_day(trade_date):
            self._cache[key] = None
            return None

        # Look up holiday overrides for ETH/ALL handling
        sql = (
            "SELECT market_status, early_close_time, late_open_time "
            "FROM trading_holidays "
            "WHERE calendar_id = %s AND holiday_date = %s"
        )
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (self.calendar_id, trade_date))
                holiday_row = cur.fetchone()

        early_close = holiday_row["early_close_time"] if holiday_row else None
        late_open = holiday_row["late_open_time"] if holiday_row else None

        result = self._build_trading_hours(
            trade_date, session_type, early_close, late_open
        )
        self._cache[key] = result
        return result

    def get_expected_bar_count(
        self,
        start_date: date,
        end_date: date,
        timeframe_minutes: int = 1,
        session_type: SessionType = SessionType.RTH,
    ) -> int:
        """
        Calculate expected number of bars between two dates (inclusive).

        Uses ZoneInfo-aware datetimes so DST transitions are handled correctly.
        """
        self._ensure_loaded()
        total_bars = 0
        current_date = start_date

        while current_date <= end_date:
            hours = self.get_trading_hours(current_date, session_type)
            if hours is not None:
                duration = hours.session_end - hours.session_start
                minutes = int(duration.total_seconds() / 60)
                total_bars += minutes // timeframe_minutes
            current_date += timedelta(days=1)

        return total_bars

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_trading_hours(
        self,
        trade_date: date,
        session_type: SessionType,
        early_close: time | None,
        late_open: time | None,
    ) -> TradingHours | None:
        """Build a TradingHours from calendar metadata + holiday overrides.

        RTH path delegates to populate_trading_sessions for algorithm parity.
        ETH/ALL paths use calendar metadata directly (not stored in table).
        """
        tz = self.timezone

        if session_type == SessionType.RTH:
            from manta_trading.data.base.session_population import (
                populate_trading_sessions,
            )

            calendars_row = {
                "timezone": str(tz),
                "market_open": self.market_open_time,
                "market_close": self.market_close_time,
            }
            holiday_row: list[dict[str, Any]] = []
            if early_close is not None or late_open is not None:
                holiday_row = [
                    {
                        "holiday_date": trade_date,
                        "market_status": "early_close" if early_close else "late_open",
                        "early_close_time": early_close,
                        "late_open_time": late_open,
                    }
                ]
            rows = populate_trading_sessions(
                self.calendar_id, trade_date, trade_date, calendars_row, holiday_row
            )
            if not rows:
                return None
            row = rows[0]
            return TradingHours(
                session_start=row["session_open_utc"].astimezone(tz),
                session_end=row["session_close_utc"].astimezone(tz),
                session_type=session_type,
                is_trading_day=True,
            )
        elif session_type == SessionType.ETH:
            if not self.has_extended_hours:
                return None
            open_t = self.extended_open_time
            close_t = self.extended_close_time
        else:
            # ALL: earliest open to latest close, with overrides
            rth_open = late_open if late_open else self.market_open_time
            rth_close = early_close if early_close else self.market_close_time
            if self.has_extended_hours:
                open_t = min(self.extended_open_time, rth_open)
                close_t = max(self.extended_close_time, rth_close)
            else:
                open_t = rth_open
                close_t = rth_close

        session_start = datetime.combine(trade_date, open_t, tzinfo=tz)
        session_end = datetime.combine(trade_date, close_t, tzinfo=tz)

        return TradingHours(
            session_start=session_start,
            session_end=session_end,
            session_type=session_type,
            is_trading_day=True,
        )
