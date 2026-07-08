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
) -> GapRow | None:
    """Return the most recent actionable gap row (by gap_end DESC), or None.

    Only rows with fetch_status IN (UNKNOWN, FAILED_RETRYABLE) are returned.

    Args:
        conn:        Open psycopg connection.
        symbol:      Instrument ticker.
        granularity: 'daily' or 'minute'.
        from_ts:     Window start (UTC, inclusive).
        to_ts:       Window end (UTC, inclusive).

    Returns:
        The most recent actionable GapRow, or None if no actionable gap exists.
    """
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
             ORDER BY gap_end DESC
             LIMIT 1
            """,
            (symbol, granularity, _ACTIONABLE_STATUSES, from_ts, to_ts),
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
