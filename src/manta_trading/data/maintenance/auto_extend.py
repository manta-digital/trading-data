"""Automated trading_sessions horizon extension (slice 147 Decision D).

Provides ``maybe_extend_trading_sessions`` — a pure helper that checks
each calendar's horizon and extends when needed. Called from:
  - ``mt data status`` (every invocation; no gating needed — cheap).
  - Daemon idle tick via ``Runner.register_idle_hook`` (gated 24h in-process).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from manta_trading.constants import (
    TRADING_SESSIONS_EXTENSION_YEARS,
    TRADING_SESSIONS_HORIZON_WARN_DAYS,
)
from manta_trading.data.base.session_population import populate_trading_sessions
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    import psycopg

_logger = get_logger(__name__)

# In-process 24h gate used by the daemon idle-tick path.
# Status-command callers use bypass_gate=True (implicitly — no gating needed).
_last_extend_at: datetime | None = None


@dataclass
class AutoExtendResult:
    """Result of a maybe_extend_trading_sessions call."""

    triggered: bool
    calendars_extended: list[str] = field(default_factory=list)
    rows_inserted: int = 0
    horizon_after: dict[str, date] = field(default_factory=dict)
    error: str | None = None


def maybe_extend_trading_sessions(
    conn_factory: Callable[[], "psycopg.Connection[Any]"],
    *,
    bypass_gate: bool = False,
) -> AutoExtendResult:
    """Extend trading_sessions for each calendar whose horizon is short.

    Args:
        conn_factory: Callable returning a psycopg connection (not pooled —
            caller provides the factory; connections are opened per-calendar).
        bypass_gate: When True, skip the 24h in-process gate check. Use for
            status-command callers (every invocation is fine; cheap MAX query).
            Daemon callers leave this False (default).

    Returns:
        AutoExtendResult describing what happened.
    """
    global _last_extend_at  # noqa: PLW0603

    if not bypass_gate and _last_extend_at is not None:
        elapsed = datetime.now() - _last_extend_at
        if elapsed < timedelta(hours=24):
            _logger.debug("auto_extend: gated (last ran %s ago)", elapsed)
            return AutoExtendResult(triggered=False)

    today = date.today()
    current_year = datetime.now().year
    end_year = current_year + TRADING_SESSIONS_EXTENSION_YEARS
    end_date = date(end_year, 12, 31)
    threshold = today + timedelta(days=TRADING_SESSIONS_HORIZON_WARN_DAYS)

    result = AutoExtendResult(triggered=False)
    any_error = False

    with conn_factory() as conn:
        from psycopg.rows import dict_row

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT calendar_id, timezone, market_open, market_close "
                "FROM trading_calendars ORDER BY calendar_id"
            )
            calendars = cur.fetchall()

    for cal_row in calendars:
        cal_id: str = cal_row["calendar_id"]

        with conn_factory() as conn:
            from psycopg.rows import dict_row

            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT MAX(session_date) AS max_date "
                    "FROM trading_sessions WHERE calendar_id = %s",
                    (cal_id,),
                )
                max_row = cur.fetchone()
                max_date: date | None = max_row["max_date"] if max_row else None

            if max_date is not None and max_date >= threshold:
                # Horizon is healthy for this calendar.
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT MAX(session_date) AS max_date "
                        "FROM trading_sessions WHERE calendar_id = %s",
                        (cal_id,),
                    )
                    final_row = cur.fetchone()
                    if final_row and final_row["max_date"]:
                        result.horizon_after[cal_id] = final_row["max_date"]
                continue

            # Need to extend.
            start_date = (
                (max_date + timedelta(days=1)) if max_date else date(current_year, 1, 1)
            )

            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT holiday_date, market_status, "
                    "       early_close_time, late_open_time "
                    "FROM trading_holidays WHERE calendar_id = %s",
                    (cal_id,),
                )
                holidays = cur.fetchall()

        calendars_row = {
            "timezone": cal_row["timezone"],
            "market_open": cal_row["market_open"],
            "market_close": cal_row["market_close"],
        }
        holidays_rows = [
            {
                "holiday_date": h["holiday_date"],
                "market_status": h["market_status"],
                "early_close_time": h["early_close_time"],
                "late_open_time": h["late_open_time"],
            }
            for h in holidays
        ]

        if start_date > end_date:
            continue

        try:
            rows = populate_trading_sessions(
                cal_id, start_date, end_date, calendars_row, holidays_rows
            )
        except Exception as exc:
            _logger.exception(
                "auto_extend: populate_trading_sessions failed for %s", cal_id
            )
            result.error = str(exc)
            any_error = True
            continue

        if not rows:
            continue

        try:
            with conn_factory() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO trading_sessions
                            (calendar_id, session_date,
                             session_open_utc, session_close_utc)
                        VALUES (%(calendar_id)s, %(session_date)s,
                                %(session_open_utc)s, %(session_close_utc)s)
                        ON CONFLICT (calendar_id, session_date) DO UPDATE
                            SET session_open_utc  = EXCLUDED.session_open_utc,
                                session_close_utc = EXCLUDED.session_close_utc
                        """,
                        rows,
                    )
                    inserted = cur.rowcount
                conn.commit()
        except Exception as exc:
            _logger.exception("auto_extend: INSERT batch failed for %s", cal_id)
            result.error = str(exc)
            any_error = True
            continue

        result.triggered = True
        result.calendars_extended.append(cal_id)
        result.rows_inserted += inserted
        _logger.info(
            "auto_extend: extended %s by %d rows (horizon now %s)",
            cal_id,
            inserted,
            rows[-1]["session_date"],
        )

        # Record horizon_after for extended calendars.
        result.horizon_after[cal_id] = rows[-1]["session_date"]

    # Only advance the gate if no errors occurred.
    if not any_error:
        _last_extend_at = datetime.now()

    return result
