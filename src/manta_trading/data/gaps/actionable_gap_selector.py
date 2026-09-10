"""Select the most recent actionable gap for a (symbol, granularity) window.

An actionable gap is one whose fetch_status is UNKNOWN or FAILED_RETRYABLE
— statuses that warrant another fetch attempt.  PROVIDER_HOLE and
RETRY_EXHAUSTED rows are terminal and excluded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from manta_trading.data.quality.fetch_status import FetchStatus

if TYPE_CHECKING:
    import psycopg

_ACTIONABLE_STATUSES: list[str] = [
    str(FetchStatus.UNKNOWN),
    str(FetchStatus.FAILED_RETRYABLE),
]


@dataclass(frozen=True)
class GapRow:
    """One row from data_gaps returned by pick_most_recent_actionable_gap."""

    symbol: str
    granularity: str
    gap_start: datetime
    gap_end: datetime
    fetch_status: str
    last_attempt_ts: datetime | None
    attempt_count: int


def pick_most_recent_actionable_gap(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    from_ts: datetime,
    to_ts: datetime,
    min_gap_end: datetime | None = None,
) -> GapRow | None:
    """Return the most recent actionable gap row (by gap_end DESC), or None.

    Only rows with fetch_status IN (UNKNOWN, FAILED_RETRYABLE) are returned.

    Args:
        conn:        Open psycopg connection.
        symbol:      Instrument ticker.
        granularity: 'daily' or 'minute'.
        from_ts:     Window start (UTC, inclusive).
        to_ts:       Window end (UTC, inclusive).
        min_gap_end: Optional floor on ``gap_end`` (UTC, inclusive). When
                     given, only gaps ending at or after it are considered —
                     slice 921's trailing phase passes
                     ``now - MINUTE_TRAILING_PRIORITY_WINDOW`` so it attempts
                     the current session before any deep backfill. Omitting it
                     (every pre-921 caller) behaves exactly as before.

    Returns:
        The most recent actionable GapRow, or None if no actionable gap exists.
    """
    # Built as a list so the optional predicate does not have to be expressed
    # as an always-true default value — a NULL floor and "no floor" are
    # different questions, and only one of them belongs in the SQL.
    params: list[object] = [symbol, granularity, _ACTIONABLE_STATUSES, from_ts, to_ts]
    min_gap_end_predicate = ""
    if min_gap_end is not None:
        min_gap_end_predicate = "               AND gap_end >= %s\n"
        params.append(min_gap_end)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, granularity, gap_start, gap_end,
                   fetch_status, last_attempt_ts, attempt_count
              FROM data_gaps
             WHERE symbol = %s
               AND granularity = %s
               AND fetch_status = ANY(%s)
               AND gap_start >= %s
               AND gap_end <= %s
"""
            + min_gap_end_predicate
            + """             ORDER BY gap_end DESC
             LIMIT 1
            """,
            tuple(params),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return GapRow(
        symbol=row[0],
        granularity=row[1],
        gap_start=row[2],
        gap_end=row[3],
        fetch_status=row[4],
        last_attempt_ts=row[5],
        attempt_count=row[6],
    )
