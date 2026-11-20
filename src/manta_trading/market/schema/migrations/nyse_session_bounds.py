"""Migration 068 (slice 174 D8, #26): extended session bounds, explicit NYSE holidays.

Five steps in the runner's transaction, so any failed check rolls everything
back:

1. add ``trading_sessions.extended_open_utc``/``extended_close_utc`` and
   ``trading_holidays.extended_close_time``;
2. replace the NYSE and NASDAQ ``trading_holidays`` rows with the explicit set
   from ``nyse_calendar_history``;
3. set both calendars' ``holidays_seeded_through`` to
   ``NYSE_HOLIDAYS_SEEDED_THROUGH``;
4. upsert both calendars' sessions through ``extend_calendar_sessions`` (open,
   close and both extended bounds) from their first stored session, and delete
   the sessions the plan closes;
5. refuse if any NYSE/NASDAQ session is left without an extended bound.

The plan is computed before step 1, from the stored rows. Gap rows over the
changed dates are reconciled by ``scripts/cutover_174_calendar.py``, not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from manta_trading.data.base.session_extension import extend_calendar_sessions
from manta_trading.market.schema.nyse_calendar_history import (
    NYSE_HOLIDAYS_SEEDED_THROUGH,
    US_EQUITY_CALENDAR_IDS,
    us_equity_holidays,
)
from manta_trading.market.schema.nyse_session_plan import plan_nyse_session_changes

if TYPE_CHECKING:
    import psycopg

_ADD_COLUMNS_SQL = """
    ALTER TABLE trading_sessions
        ADD COLUMN extended_open_utc  timestamptz,
        ADD COLUMN extended_close_utc timestamptz;
    ALTER TABLE trading_holidays ADD COLUMN extended_close_time time;
"""

_HOLIDAY_INSERT_SQL = """
    INSERT INTO trading_holidays
        (calendar_id, holiday_date, holiday_name, market_status,
         early_close_time, late_open_time, extended_close_time)
    VALUES (%(calendar_id)s, %(holiday_date)s, %(holiday_name)s,
            %(market_status)s, %(early_close_time)s, %(late_open_time)s,
            %(extended_close_time)s)
"""


def apply_nyse_session_bounds(conn: psycopg.Connection[Any]) -> None:
    """Python callable for migration 068 (the five steps in the docstring)."""
    plan = plan_nyse_session_changes(conn)
    calendar_ids = list(US_EQUITY_CALENDAR_IDS)
    with conn.cursor() as cur:
        cur.execute(_ADD_COLUMNS_SQL)
        cur.execute(
            "DELETE FROM trading_holidays WHERE calendar_id = ANY(%s)", (calendar_ids,)
        )
        for calendar_id in calendar_ids:
            rows = [
                {**row, "market_status": row["market_status"].value}
                for row in us_equity_holidays(calendar_id)
            ]
            cur.executemany(_HOLIDAY_INSERT_SQL, rows)
        cur.execute(
            "UPDATE trading_calendars SET holidays_seeded_through = %s "
            "WHERE calendar_id = ANY(%s)",
            (NYSE_HOLIDAYS_SEEDED_THROUGH, calendar_ids),
        )
    for calendar_id in calendar_ids:
        _rewrite_sessions(conn, calendar_id, sorted(plan[calendar_id].closed))
    _refuse_missing_extended_bounds(conn, calendar_ids)


def _rewrite_sessions(
    conn: psycopg.Connection[Any], calendar_id: str, closed: list[Any]
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(session_date) FROM trading_sessions WHERE calendar_id = %s",
            (calendar_id,),
        )
        row = cur.fetchone()
        first = row[0] if row else None
        if first is None:
            raise RuntimeError(f"migration 068: {calendar_id} has no sessions")
        cur.execute(
            "DELETE FROM trading_sessions "
            "WHERE calendar_id = %s AND session_date = ANY(%s)",
            (calendar_id, closed),
        )
    extend_calendar_sessions(
        conn, calendar_id, start=first, end=NYSE_HOLIDAYS_SEEDED_THROUGH
    )


def _refuse_missing_extended_bounds(
    conn: psycopg.Connection[Any], calendar_ids: list[str]
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT calendar_id, MIN(session_date), COUNT(*) FROM trading_sessions "
            "WHERE calendar_id = ANY(%s) "
            "AND (extended_open_utc IS NULL OR extended_close_utc IS NULL) "
            "GROUP BY calendar_id",
            (calendar_ids,),
        )
        missing = cur.fetchall()
    if missing:
        detail = ", ".join(f"{cal}: {n} from {first}" for cal, first, n in missing)
        raise RuntimeError(
            f"migration 068: sessions left without extended bounds ({detail})"
        )
