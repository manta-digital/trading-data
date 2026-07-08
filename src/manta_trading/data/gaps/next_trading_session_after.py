"""Lookup the next trading session after a given date."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg


def next_trading_session_after(
    conn: "psycopg.Connection[object]",
    calendar_id: str,
    after_date: date,
) -> date | None:
    """Return the earliest session_date in trading_sessions after after_date.

    Args:
        conn:        Open psycopg connection.
        calendar_id: Trading calendar identifier (e.g. 'US').
        after_date:  Lower bound; the returned date is strictly greater.

    Returns:
        The next session date, or None if the horizon is exhausted.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(session_date) FROM trading_sessions "
            "WHERE calendar_id = %s AND session_date > %s",
            (calendar_id, after_date),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0]
