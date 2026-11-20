"""What slice 174's calendar migration would change, read-only (the dry check).

:func:`plan_nyse_session_changes` computes NYSE and NASDAQ sessions from the
explicit holiday table and compares them with the stored ones. The migration
deletes the dates it closes; the cutover prints it, refuses to apply when it
differs from ``EXPECTED_SESSION_CHANGES``, and requires it empty afterwards.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

from manta_trading.data.base.session_extension import has_extended_columns
from manta_trading.data.base.session_population import populate_trading_sessions
from manta_trading.market.schema.nyse_calendar_history import (
    NYSE_HOLIDAYS_SEEDED_THROUGH,
    US_EQUITY_CALENDAR_IDS,
    us_equity_holidays,
)
from manta_trading.market.schema.nyse_session_changes import SessionChanges

if TYPE_CHECKING:
    import psycopg

SessionChangePlan = dict[str, SessionChanges]
"""Per calendar id (NYSE, NASDAQ), the changes the migration makes."""

_BOUNDS = ("session_open_utc", "session_close_utc")
_EXTENDED_BOUNDS = ("extended_open_utc", "extended_close_utc")


def plan_nyse_session_changes(conn: psycopg.Connection[Any]) -> SessionChangePlan:
    """The session changes per US equity calendar. Writes nothing.

    Extended bounds are compared only once the columns exist (after the
    migration); before it, a session is retimed when its open or close moves.

    Raises:
        ValueError: a calendar has no stored sessions or no calendar row.
    """
    extended = has_extended_columns(conn)
    return {
        calendar_id: _plan_calendar(conn, calendar_id, extended)
        for calendar_id in US_EQUITY_CALENDAR_IDS
    }


def _plan_calendar(
    conn: psycopg.Connection[Any], calendar_id: str, extended: bool
) -> SessionChanges:
    compared = _BOUNDS + _EXTENDED_BOUNDS if extended else _BOUNDS
    current = _read_sessions(conn, calendar_id, compared)
    if not current:
        raise ValueError(f"calendar {calendar_id!r} has no stored sessions")
    first, horizon = min(current), max(current)
    new = {
        row["session_date"]: row
        for row in populate_trading_sessions(
            calendar_id,
            first,
            NYSE_HOLIDAYS_SEEDED_THROUGH,
            _read_calendar_row(conn, calendar_id),
            us_equity_holidays(calendar_id),
        )
    }
    added = new.keys() - current.keys()
    return SessionChanges(
        opened=frozenset(d for d in added if d <= horizon),
        closed=frozenset(current.keys() - new.keys()),
        retimed=frozenset(
            d
            for d in current.keys() & new.keys()
            if any(current[d][col] != new[d][col] for col in compared)
        ),
        appended=frozenset(d for d in added if d > horizon),
    )


def _read_sessions(
    conn: psycopg.Connection[Any], calendar_id: str, columns: tuple[str, ...]
) -> dict[date, dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT session_date, {', '.join(columns)} FROM trading_sessions "
            "WHERE calendar_id = %s",
            (calendar_id,),
        )
        return {row["session_date"]: row for row in cur.fetchall()}


def _read_calendar_row(
    conn: psycopg.Connection[Any], calendar_id: str
) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT timezone, market_open, market_close, has_extended_hours, "
            "extended_open, extended_close FROM trading_calendars "
            "WHERE calendar_id = %s",
            (calendar_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"calendar {calendar_id!r} is not in trading_calendars")
    return dict(row)
