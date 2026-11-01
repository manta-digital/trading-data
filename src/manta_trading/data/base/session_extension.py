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

from manta_trading.data.base.session_eras import (
    is_dated,
    plan_segments,
    read_eras,
    with_sunday_opens,
)
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

    A dated calendar (301 D3) is populated one era segment at a time with each
    era's hours; every other calendar with its ``trading_calendars`` hours.

    Raises:
        ValueError: ``calendar_id`` is not in ``trading_calendars``.
        NoHoursEraError: the calendar is dated and has no era for ``start``.
    """
    calendars_row, seeded_through = _read_calendar(conn, calendar_id)
    clamped = end > seeded_through
    effective_end = min(end, seeded_through)

    rows_upserted = 0
    if start <= effective_end:
        rows = _populate(conn, calendar_id, start, effective_end, calendars_row)
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


def _populate(
    conn: psycopg.Connection[Any],
    calendar_id: str,
    start: date,
    end: date,
    calendars_row: dict[str, Any],
) -> list[dict[str, Any]]:
    """Session rows for ``[start, end]``, split at era boundaries when dated."""
    holidays = _read_holidays(conn, calendar_id)
    if not is_dated(conn, calendar_id):
        return populate_trading_sessions(
            calendar_id, start, end, calendars_row, holidays
        )
    rows: list[dict[str, Any]] = []
    for segment in plan_segments(calendar_id, start, end, read_eras(conn, calendar_id)):
        era_row = {
            **calendars_row,
            "market_open": segment.era.market_open,
            "market_close": segment.era.market_close,
        }
        rows += populate_trading_sessions(
            calendar_id,
            segment.start,
            segment.end,
            era_row,
            with_sunday_opens(holidays, segment),
        )
    return rows


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
