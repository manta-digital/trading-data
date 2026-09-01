"""Compute missing trading-session ranges for a (symbol, granularity) window.

Implements the gap-function algorithm from slice-145 arch §"Gap function"
steps 1–6:
  1. Clamp the window to the instrument's lifecycle dates.
  2. Fetch ordered trading sessions in the clamped window.
  3. Fetch stored bar timestamps in the clamped window.
  4. Set-difference: sessions without a bar.
  5. Group contiguous missing sessions into GapRange spans.
  6. Return the list of GapRange objects.

Pure read — no writes.  Caller must supply an open psycopg connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

# Table name for each granularity (string constants — not user-supplied).
_DATA_TABLE: dict[str, str] = {
    "daily": "daily_ohlcv",
    "minute": "minute_ohlcv",
}

# Column that represents the bar's session timestamp in each table.
# Both daily_ohlcv and minute_ohlcv use 'time' (timestamptz).
_TS_COL: dict[str, str] = {
    "daily": "time",
    "minute": "time",
}


@dataclass(frozen=True)
class GapRange:
    """One contiguous missing-data range for a (symbol, granularity) pair."""

    symbol: str
    granularity: str
    gap_start_utc: datetime
    gap_end_utc: datetime


def compute_missing_ranges(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    from_ts: datetime,
    to_ts: datetime,
) -> list[GapRange]:
    """Return GapRange objects representing sessions with no stored bar.

    Args:
        conn:        Open psycopg connection.
        symbol:      Instrument ticker.
        granularity: 'daily' or 'minute'.
        from_ts:     Window start (UTC, inclusive).
        to_ts:       Window end (UTC, inclusive).

    Returns:
        Ordered list of GapRange objects (earliest first).  Empty list if
        the window is fully covered or if the instrument has no lifecycle
        dates.

    Raises:
        ValueError: If granularity is not 'daily' or 'minute'.
    """
    if granularity not in _DATA_TABLE:
        raise ValueError(
            f"granularity must be 'daily' or 'minute', got {granularity!r}"
        )

    # Step 1 — lifecycle-date clamping
    clamped_from, clamped_to = clamp_to_lifecycle(conn, symbol, from_ts, to_ts)
    if clamped_from is None:
        # No lifecycle dates available — cannot compute ranges
        return []

    # Step 2 — ordered trading sessions in clamped window
    sessions = fetch_sessions(conn, symbol, clamped_from, clamped_to)
    if not sessions:
        return []

    # Step 3 — stored bar timestamps
    stored = _fetch_stored_timestamps(
        conn, symbol, granularity, clamped_from, clamped_to
    )

    # Step 4 — set difference
    missing = [s for s in sessions if s not in stored]
    if not missing:
        return []

    # Step 5 — group contiguous runs into GapRange spans
    return group_sessions_into_ranges(symbol, granularity, missing, sessions)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def clamp_to_lifecycle(
    conn: "psycopg.Connection[object]",
    symbol: str,
    from_ts: datetime,
    to_ts: datetime,
) -> tuple[datetime, datetime] | tuple[None, None]:
    """Clamp window to [first_listing_date, delisted_date or to_ts].

    Returns (None, None) if neither first_listing_date nor first_data_date
    is available (the instrument has no lifecycle anchor).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT first_listing_date, first_data_date, delisted_date "
            "FROM instruments WHERE symbol = %s",
            (symbol,),
        )
        row = cur.fetchone()

    if row is None:
        return None, None

    first_listing_date, first_data_date, delisted_date = row

    # Determine effective lower bound
    anchor = first_listing_date or first_data_date
    if anchor is None:
        return None, None

    # Convert date → UTC datetime (midnight UTC) if needed
    lower = _date_to_utc(anchor)
    effective_from = max(from_ts, lower)

    # Clamp upper bound to delisted_date if set
    if delisted_date is not None:
        upper = _date_to_utc(delisted_date)
        effective_to = min(to_ts, upper)
    else:
        effective_to = to_ts

    if effective_from > effective_to:
        return None, None

    return effective_from, effective_to


def fetch_sessions(
    conn: "psycopg.Connection[object]",
    symbol: str,
    from_ts: datetime,
    to_ts: datetime,
) -> list[datetime]:
    """Return ordered list of session_open_utc datetimes in the window."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts.session_open_utc
              FROM trading_sessions ts
              JOIN instruments i
                ON i.trading_calendar_id = ts.calendar_id
             WHERE i.symbol = %s
               AND ts.session_open_utc >= %s
               AND ts.session_open_utc <= %s
             ORDER BY ts.session_open_utc
            """,
            (symbol, from_ts, to_ts),
        )
        return [row[0] for row in cur.fetchall()]


def _fetch_stored_timestamps(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    from_ts: datetime,
    to_ts: datetime,
) -> set[datetime]:
    """Return the set of bar timestamps stored in the data table."""
    table = _DATA_TABLE[granularity]
    ts_col = _TS_COL[granularity]
    # Table names are internal constants — safe to interpolate.
    sql = (
        f"SELECT {ts_col} FROM {table} "  # noqa: S608
        f"WHERE symbol = %s AND {ts_col} >= %s AND {ts_col} <= %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (symbol, from_ts, to_ts))
        return {row[0] for row in cur.fetchall()}


def group_sessions_into_ranges(
    symbol: str,
    granularity: str,
    missing: list[datetime],
    all_sessions: list[datetime],
) -> list[GapRange]:
    """Group contiguous missing session timestamps into GapRange spans.

    Two sessions are "contiguous" if there is no trading session between
    them (i.e. they are adjacent in the all_sessions list).
    """
    session_set = set(all_sessions)
    session_index: dict[datetime, int] = {s: i for i, s in enumerate(all_sessions)}

    ranges: list[GapRange] = []
    run_start: datetime | None = None
    prev_missing_idx: int | None = None

    for ts in missing:
        idx = session_index[ts]
        if run_start is None:
            run_start = ts
            prev_missing_idx = idx
        elif idx == prev_missing_idx + 1:
            # Contiguous with previous missing session
            prev_missing_idx = idx
        else:
            # Gap in sessions — close the current run and start a new one
            assert run_start is not None
            ranges.append(
                GapRange(
                    symbol=symbol,
                    granularity=granularity,
                    gap_start_utc=run_start,
                    gap_end_utc=missing[missing.index(ts) - 1],
                )
            )
            run_start = ts
            prev_missing_idx = idx

    if run_start is not None:
        ranges.append(
            GapRange(
                symbol=symbol,
                granularity=granularity,
                gap_start_utc=run_start,
                gap_end_utc=missing[-1],
            )
        )

    return ranges


def _date_to_utc(d: object) -> datetime:
    """Convert a date or datetime to UTC midnight datetime."""
    from datetime import date

    if isinstance(d, datetime):
        return (
            d.replace(tzinfo=timezone.utc)
            if d.tzinfo is None
            else d.astimezone(timezone.utc)
        )
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    raise TypeError(f"Expected date or datetime, got {type(d)!r}")
