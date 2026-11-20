"""Read session-aligned equity bars (slice 174 D1, D2, D5, D6).

Built at read time from eligible 1m bars: the symbol's ``trading_sessions``
rows in ``[start, end]`` give the phases (``session_phases``), the statement
in ``session_bars_sql`` buckets the minutes, and adjustment is applied by the
calendar's local date, which for a session bar is its session date.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import psycopg

from manta_trading.constants import (
    GRANULARITY_BAR_MINUTES,
    Granularity,
    PrintMode,
    SessionMode,
)
from manta_trading.data.adjustment import adjusted as adjusted_fn
from manta_trading.data.base.symbol_calendar import SymbolCalendar, symbol_calendar
from manta_trading.data.base.trading_calendar import OutOfHorizonError
from manta_trading.market.session_bars_sql import SESSION_BAR_COLUMNS, session_bars_sql
from manta_trading.market.session_phases import PhaseInterval, SessionBounds, phases_for

PRICES = ("open", "high", "low", "close")


class MissingExtendedBoundsError(RuntimeError):
    """An extended read met a session row without extended bounds (174 D6).

    Every NYSE/NASDAQ row has them after migration 068, so this is broken data,
    not an empty answer: it surfaces as a logged 500.
    """


def session_bounds(
    conn: psycopg.Connection[Any],
    calendar_id: str,
    start: date,
    end: date,
    mode: SessionMode,
) -> list[SessionBounds]:
    """Session rows in ``[start, end]``. Regular reads never touch the
    extended columns (the session ceiling is measured before they exist)."""
    extended = mode is SessionMode.EXTENDED
    columns = "session_date, session_open_utc, session_close_utc"
    if extended:
        columns += ", extended_open_utc, extended_close_utc"
    rows = conn.execute(
        f"SELECT {columns} FROM trading_sessions WHERE calendar_id = %s "
        "AND session_date BETWEEN %s AND %s ORDER BY session_date",
        (calendar_id, start, end),
    ).fetchall()
    return [
        SessionBounds(
            session_date=row[0],
            open=row[1],
            close=row[2],
            extended_open=row[3] if extended else None,
            extended_close=row[4] if extended else None,
        )
        for row in rows
    ]


def check_horizon(calendar: SymbolCalendar, end: date) -> None:
    """Past the holiday bound the calendar does not know closures (174 D6)."""
    if end > calendar.holidays_seeded_through:
        raise OutOfHorizonError(
            calendar.calendar_id, end, calendar.holidays_seeded_through
        )


def session_phases(
    symbol: str, bounds: list[SessionBounds], mode: SessionMode
) -> list[tuple[date, PhaseInterval]]:
    """Every phase the read covers, with its session date.

    Raises:
        MissingExtendedBoundsError: an extended read over a row without both
            extended bounds.
    """
    if mode is SessionMode.EXTENDED:
        broken = [
            b.session_date
            for b in bounds
            if b.extended_open is None or b.extended_close is None
        ]
        if broken:
            raise MissingExtendedBoundsError(
                f"{symbol}: no extended bounds for session {broken[0]}"
                f" ({len(broken)} sessions)"
            )
    return [
        (session.session_date, interval)
        for session in bounds
        for interval in phases_for(session, mode)
    ]


def read_session_bars(
    conn: psycopg.Connection[Any],
    symbol: str,
    granularity: Granularity,
    mode: SessionMode,
    start: date,
    end: date,
    *,
    adjusted: bool,
    prints: PrintMode,
) -> pd.DataFrame:
    """Session bars indexed by bucket start (UTC), with OHLCV, ``session_date``
    and ``phase``.

    Raises:
        OutOfHorizonError: ``end`` is past the calendar's holiday bound.
        MissingExtendedBoundsError: an extended read over a row without them.
        ValueError: ``mode`` is ``utc``, or the symbol has no calendar.
    """
    if mode is SessionMode.UTC:
        raise ValueError("read_session_bars needs a session mode, not utc")
    calendar = symbol_calendar(conn, symbol)
    check_horizon(calendar, end)
    phases = session_phases(
        symbol, session_bounds(conn, calendar.calendar_id, start, end, mode), mode
    )
    if not phases:
        return _frame([])
    params = {
        "symbol": symbol,
        "width": timedelta(minutes=GRANULARITY_BAR_MINUTES[granularity]),
        "lo": min(p.start for _, p in phases),
        "hi": max(p.end for _, p in phases),
        "dates": [d for d, _ in phases],
        "phases": [str(p.phase) for _, p in phases],
        "starts": [p.start for _, p in phases],
        "ends": [p.end for _, p in phases],
    }
    df = _frame(conn.execute(session_bars_sql(prints), params).fetchall())
    if adjusted and not df.empty:
        df = adjusted_fn(df, symbol, conn, dates_in=calendar.zone)
    return df


def _frame(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(SESSION_BAR_COLUMNS))
    df["bucket"] = pd.to_datetime(df["bucket"], utc=True)
    df = df.set_index("bucket")
    df[list(PRICES)] = df[list(PRICES)].astype("float64")
    df["volume"] = df["volume"].astype("int64")
    return df
