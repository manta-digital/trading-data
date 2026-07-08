"""Pure function for generating trading_sessions rows.

Extracted from TradingCalendar._build_trading_hours so that both
the migration 026 population job and the TradingCalendar class consume
the same algorithm — no second implementation.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from manta_trading.data.base.trading_calendar import MarketStatus


def populate_trading_sessions(
    calendar_id: str,
    start_date: date,
    end_date: date,
    calendars_row: dict[str, Any],
    holidays_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate trading_sessions rows for [start_date, end_date] (inclusive).

    Args:
        calendar_id: Calendar identifier (e.g. 'NYSE').
        start_date: First date of the range to populate.
        end_date: Last date of the range to populate (inclusive).
        calendars_row: Dict with keys ``timezone`` (str), ``market_open``
            (time), ``market_close`` (time).
        holidays_rows: List of dicts with keys ``holiday_date`` (date),
            ``market_status`` (str), ``early_close_time`` (time | None),
            ``late_open_time`` (time | None).

    Returns:
        List of dicts with keys ``calendar_id``, ``session_date``,
        ``session_open_utc``, ``session_close_utc`` — one entry per
        trading day. Weekends and ``market_status='closed'`` holidays
        are absent.
    """
    tz = ZoneInfo(calendars_row["timezone"])
    default_open: time = calendars_row["market_open"]
    default_close: time = calendars_row["market_close"]

    # Index holidays by date for O(1) lookup.
    holiday_index: dict[date, dict[str, Any]] = {
        row["holiday_date"]: row for row in holidays_rows
    }

    rows: list[dict[str, Any]] = []
    current = start_date
    one_day = timedelta(days=1)

    while current <= end_date:
        # Skip weekends (Mon=0 … Sun=6; Sat=5, Sun=6)
        if current.weekday() >= 5:
            current += one_day
            continue

        holiday = holiday_index.get(current)
        if holiday is not None:
            status = MarketStatus(holiday["market_status"])
            if status == MarketStatus.CLOSED:
                current += one_day
                continue
            open_t: time = holiday.get("late_open_time") or default_open
            close_t: time = holiday.get("early_close_time") or default_close
        else:
            open_t = default_open
            close_t = default_close

        session_open_utc = datetime.combine(current, open_t, tzinfo=tz).astimezone(
            ZoneInfo("UTC")
        )
        session_close_utc = datetime.combine(current, close_t, tzinfo=tz).astimezone(
            ZoneInfo("UTC")
        )

        rows.append(
            {
                "calendar_id": calendar_id,
                "session_date": current,
                "session_open_utc": session_open_utc,
                "session_close_utc": session_close_utc,
            }
        )
        current += one_day

    return rows
