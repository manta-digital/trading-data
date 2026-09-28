"""Pure functions for generating trading_sessions rows.

Extracted from TradingCalendar._build_trading_hours so that both
the migration 026 population job and the TradingCalendar class consume
the same algorithm — no second implementation.

A session is dated by the day it closes. When a calendar's open time is later
than its close time (CME Globex: 17:00 → 16:00), the open falls on the previous
calendar day. :func:`session_interval` is the one place that rule lives.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from manta_trading.data.base.trading_calendar import MarketStatus

_UTC = ZoneInfo("UTC")


def session_interval(
    session_date: date, open_t: time, close_t: time, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """Return the UTC ``(open, close)`` of the session dated ``session_date``.

    The close is ``close_t`` on ``session_date``. The open is ``open_t`` on the
    same day when ``open_t < close_t``, and on the previous day when
    ``open_t > close_t`` (a session that opens the evening before). Both are
    built in ``tz`` before conversion, so each side gets its own UTC offset
    across a DST change.

    Raises:
        ValueError: ``open_t == close_t`` — the session would be empty or a
            full day, and neither is expressible as a dated session.
    """
    if open_t == close_t:
        raise ValueError(
            f"session {session_date.isoformat()}: open time equals close time "
            f"({open_t.isoformat()})"
        )
    open_date = session_date if open_t < close_t else session_date - timedelta(days=1)
    session_open = datetime.combine(open_date, open_t, tzinfo=tz)
    session_close = datetime.combine(session_date, close_t, tzinfo=tz)
    return session_open.astimezone(_UTC), session_close.astimezone(_UTC)


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
        are absent. Each interval comes from :func:`session_interval`, so the
        open-after-close rule applies to every calendar; for a calendar whose
        open precedes its close (NYSE, NASDAQ) the output is unchanged.
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

        session_open_utc, session_close_utc = session_interval(
            current, open_t, close_t, tz
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
