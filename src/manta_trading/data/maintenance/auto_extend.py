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
from manta_trading.data.base.session_extension import (
    extend_calendar_sessions,
    session_horizon,
)
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
    #: Calendars whose extension the holiday bound stopped, mapped to that
    #: bound (slice 221 D6). The fix is seeding the next year's schedule.
    clamped: dict[str, date] = field(default_factory=dict)
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
            _logger.debug(
                "auto_extend: gated (last ran %s ago)", elapsed
            )
            return AutoExtendResult(triggered=False)

    today = date.today()
    current_year = datetime.now().year
    end_date = date(current_year + TRADING_SESSIONS_EXTENSION_YEARS, 12, 31)
    threshold = today + timedelta(days=TRADING_SESSIONS_HORIZON_WARN_DAYS)

    result = AutoExtendResult(triggered=False)
    any_error = False

    with conn_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT calendar_id FROM trading_calendars ORDER BY calendar_id"
            )
            calendar_ids: list[str] = [row[0] for row in cur.fetchall()]

    for cal_id in calendar_ids:
        try:
            with conn_factory() as conn:
                max_date = session_horizon(conn, cal_id)
                if max_date is not None and max_date >= threshold:
                    # Horizon is healthy for this calendar.
                    result.horizon_after[cal_id] = max_date
                    continue
                start_date = (
                    max_date + timedelta(days=1)
                    if max_date
                    else date(current_year, 1, 1)
                )
                extension = extend_calendar_sessions(
                    conn, cal_id, start=start_date, end=end_date
                )
                conn.commit()
        except Exception as exc:
            _logger.exception("auto_extend: extension failed for %s", cal_id)
            result.error = str(exc)
            any_error = True
            continue

        if extension.horizon_after is not None:
            result.horizon_after[cal_id] = extension.horizon_after
        if extension.clamped_by_holiday_bound:
            result.clamped[cal_id] = extension.holidays_seeded_through
        if extension.rows_upserted == 0:
            continue

        result.triggered = True
        result.calendars_extended.append(cal_id)
        result.rows_inserted += extension.rows_upserted
        _logger.info(
            "auto_extend: extended %s by %d rows (horizon now %s)",
            cal_id,
            extension.rows_upserted,
            extension.horizon_after,
        )

    # Only advance the gate if no errors occurred.
    if not any_error:
        _last_extend_at = datetime.now()

    return result
