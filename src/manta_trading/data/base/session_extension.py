"""The one routine that writes ``trading_sessions`` forward (221 D6).

``mt data extend``, the auto-extend hook, migration 058 and (from 225) the
ingest pass all extend a calendar's sessions through
:func:`extend_calendar_sessions`. It never writes a session dated after the
calendar's ``holidays_seeded_through``: past that date the calendar's closures
are unknown, and a session written there would treat an unseeded holiday as an
ordinary trading day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

from manta_trading.data.base.session_population import populate_trading_sessions

if TYPE_CHECKING:
    import psycopg

_UPSERT_SESSIONS_SQL = """
    INSERT INTO trading_sessions
        (calendar_id, session_date, session_open_utc, session_close_utc)
    VALUES (%(calendar_id)s, %(session_date)s,
            %(session_open_utc)s, %(session_close_utc)s)
    ON CONFLICT (calendar_id, session_date) DO UPDATE
        SET session_open_utc  = EXCLUDED.session_open_utc,
            session_close_utc = EXCLUDED.session_close_utc
"""


def holiday_bound_message(
    calendar_id: str, horizon: date | None, holidays_seeded_through: date
) -> str:
    """The one wording for "the holiday bound is what stops this horizon" (D6)."""
    horizon_text = horizon.isoformat() if horizon else "none"
    return (
        f"{calendar_id}: horizon {horizon_text} — holidays seeded through "
        f"{holidays_seeded_through.isoformat()}; seed the next year's "
        f"{calendar_id} schedule"
    )


@dataclass(frozen=True)
class CalendarExtension:
    """What one :func:`extend_calendar_sessions` call did."""

    calendar_id: str
    rows_upserted: int
    horizon_after: date | None
    holidays_seeded_through: date
    clamped_by_holiday_bound: bool


def extend_calendar_sessions(
    conn: psycopg.Connection[Any],
    calendar_id: str,
    *,
    start: date,
    end: date,
) -> CalendarExtension:
    """Populate and upsert ``calendar_id``'s sessions over ``[start, end]``.

    ``end`` is clamped to the calendar's ``holidays_seeded_through``;
    ``clamped_by_holiday_bound`` reports whether the clamp applied. The caller
    owns the transaction — nothing is committed here.

    Raises:
        ValueError: ``calendar_id`` is not in ``trading_calendars``.
    """
    calendars_row, seeded_through = _read_calendar(conn, calendar_id)
    clamped = end > seeded_through
    effective_end = min(end, seeded_through)

    rows_upserted = 0
    if start <= effective_end:
        rows = populate_trading_sessions(
            calendar_id,
            start,
            effective_end,
            calendars_row,
            _read_holidays(conn, calendar_id),
        )
        if rows:
            with conn.cursor() as cur:
                cur.executemany(_UPSERT_SESSIONS_SQL, rows)
                rows_upserted = cur.rowcount

    return CalendarExtension(
        calendar_id=calendar_id,
        rows_upserted=rows_upserted,
        horizon_after=session_horizon(conn, calendar_id),
        holidays_seeded_through=seeded_through,
        clamped_by_holiday_bound=clamped,
    )


def _read_calendar(
    conn: psycopg.Connection[Any], calendar_id: str
) -> tuple[dict[str, Any], date]:
    """The calendar's population inputs and its holiday bound."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT timezone, market_open, market_close, holidays_seeded_through "
            "FROM trading_calendars WHERE calendar_id = %s",
            (calendar_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"calendar {calendar_id!r} is not in trading_calendars")
    calendars_row = {
        "timezone": row["timezone"],
        "market_open": row["market_open"],
        "market_close": row["market_close"],
    }
    return calendars_row, row["holidays_seeded_through"]


def _read_holidays(
    conn: psycopg.Connection[Any], calendar_id: str
) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT holiday_date, market_status, early_close_time, late_open_time "
            "FROM trading_holidays WHERE calendar_id = %s",
            (calendar_id,),
        )
        return list(cur.fetchall())


def session_horizon(conn: psycopg.Connection[Any], calendar_id: str) -> date | None:
    """``MAX(session_date)`` for the calendar, or ``None`` when it has none."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(session_date) FROM trading_sessions WHERE calendar_id = %s",
            (calendar_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None
