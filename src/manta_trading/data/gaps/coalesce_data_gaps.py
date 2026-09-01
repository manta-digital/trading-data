"""Coalesce adjacent data_gaps rows for a (symbol, granularity) scope.

Implements the coalesce_data_gaps algorithm from slice-145 arch
§"coalesce_data_gaps":

  Single-pass O(n) sorted-list accumulator.  Two adjacent rows are merged
  if they share the same fetch_status AND next_trading_session_after(A.gap_end)
  equals B.gap_start.

  On merge: last_attempt_ts = MIN, attempt_count = MAX.
  Idempotent: returns 0 when nothing merges.

Caller must be inside an open transaction.  An advisory lock on
(symbol, granularity) is acquired for the duration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from manta_trading.data.gaps.next_trading_session_after import (
    next_trading_session_after,
)

if TYPE_CHECKING:
    import psycopg


def coalesce_data_gaps(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
) -> int:
    """Merge adjacent same-status data_gaps rows for (symbol, granularity).

    Caller must be inside an open transaction and must already hold the
    advisory lock for (symbol, granularity).

    Returns:
        Number of rows merged (0 on idempotent re-run).
    """
    return _do_coalesce(conn, symbol, granularity)


def _do_coalesce(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
) -> int:
    rows = _fetch_rows(conn, symbol, granularity)
    if len(rows) < 2:
        return 0

    # Determine calendar_id once for next_trading_session_after lookups
    calendar_id = _fetch_calendar_id(conn, symbol)

    merged_count = 0
    result: list[dict] = [rows[0]]

    for current in rows[1:]:
        prev = result[-1]
        if _are_adjacent(conn, prev, current, calendar_id):
            # Merge current into prev
            result[-1] = {
                "gap_start": prev["gap_start"],
                "gap_end": current["gap_end"],
                "fetch_status": prev["fetch_status"],
                "last_attempt_ts": _min_ts(
                    prev["last_attempt_ts"], current["last_attempt_ts"]
                ),
                "attempt_count": max(prev["attempt_count"], current["attempt_count"]),
            }
            merged_count += 1
        else:
            result.append(current)

    if merged_count == 0:
        return 0

    # Persist: delete all rows in scope, re-insert coalesced set
    _delete_all(conn, symbol, granularity)
    _insert_rows(conn, symbol, granularity, result)

    return merged_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_rows(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT gap_start, gap_end, fetch_status, last_attempt_ts, attempt_count
              FROM data_gaps
             WHERE symbol = %s AND granularity = %s
             ORDER BY gap_start
            """,
            (symbol, granularity),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_calendar_id(
    conn: "psycopg.Connection[object]",
    symbol: str,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trading_calendar_id FROM instruments WHERE symbol = %s",
            (symbol,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"Instrument not found: {symbol!r}")
    return row[0]


def _are_adjacent(
    conn: "psycopg.Connection[object]",
    prev: dict,
    current: dict,
    calendar_id: str,
) -> bool:
    """True if prev and current share fetch_status and are session-adjacent."""
    if prev["fetch_status"] != current["fetch_status"]:
        return False

    next_session = next_trading_session_after(conn, calendar_id, prev["gap_end"].date())
    if next_session is None:
        return False

    # current.gap_start is a datetime; compare date portions
    current_start_date = current["gap_start"].date()
    return next_session == current_start_date


def _min_ts(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _delete_all(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM data_gaps WHERE symbol = %s AND granularity = %s",
            (symbol, granularity),
        )


def _insert_rows(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    rows: list[dict],
) -> None:
    now_utc = datetime.now(tz=timezone.utc)
    params = [
        (
            symbol,
            granularity,
            row["gap_start"],
            row["gap_end"],
            row["fetch_status"],
            row.get("last_attempt_ts", now_utc),
            row["attempt_count"],
        )
        for row in rows
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO data_gaps
                (symbol, granularity, gap_start, gap_end,
                 fetch_status, last_attempt_ts, attempt_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            params,
        )
