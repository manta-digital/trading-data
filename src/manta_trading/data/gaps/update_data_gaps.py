"""Transactional update of data_gaps and acquisition_state.

Implements the update_data_gaps algorithm from slice-145 arch §"update_data_gaps"
steps 1–7.  Caller must be inside an open transaction; this function does not
start its own.  The advisory lock is acquired here; caller's atomicity guarantee
is preserved because the lock is transaction-scoped.

Note on signature divergence: the arch's documented signature does not include
the `outcome` parameter.  Step 7 of the algorithm requires writing
`acquisition_state.last_attempt_outcome` to "the caller's outcome," and adding
`outcome` to the call surface is the only way to honor that without back-
channeling state through globals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.gaps.compute_missing_ranges import (
    GapRange,
    compute_missing_ranges,
)
from manta_trading.data.quality.fetch_status import FetchStatus

if TYPE_CHECKING:
    import psycopg


@dataclass(frozen=True)
class UpdateResult:
    """Summary of changes made by update_data_gaps."""

    gaps_inserted: int
    gaps_promoted_exhausted: int
    terminal_rows_reset: int


def update_data_gaps(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    from_ts: datetime,
    to_ts: datetime,
    fetch_status_for_unfilled: FetchStatus | None,
    *,
    force_reset_terminal: bool = False,
    outcome: LastAttemptOutcome,
    precomputed_ranges: list[GapRange] | None = None,
) -> UpdateResult:
    """Synchronize data_gaps and acquisition_state for one (symbol, granularity) window.

    This function acquires an advisory lock on (symbol, granularity) for the
    duration of its work.  Caller must already be inside an open transaction;
    this function does not start its own — it relies on the caller's transaction
    for atomicity.

    Args:
        conn:                     Open psycopg connection inside a transaction.
        symbol:                   Instrument ticker.
        granularity:              'daily' or 'minute'.
        from_ts:                  Window start (UTC, inclusive).
        to_ts:                    Window end (UTC, inclusive).
        fetch_status_for_unfilled: FetchStatus to assign to newly-inserted gap
                                  rows, or None if the fetch was fully successful
                                  (no unfilled rows will be inserted).
        force_reset_terminal:     If True, clear PROVIDER_HOLE and RETRY_EXHAUSTED
                                  rows in scope before carrying forward attempt counts.
                                  Used by slice 148 (mt data refetch).
        outcome:                  The caller's fetch outcome; written to
                                  acquisition_state.last_attempt_outcome.
        precomputed_ranges:       Minute-path only (slice 162). When provided,
                                  insert exactly these GapRanges instead of the
                                  single-span short-circuit — the caller (the
                                  minute daemon) has already computed the
                                  coverage-aware missing sessions via
                                  compute_missing_minute_sessions. When None
                                  (daily path, and any caller that doesn't pass
                                  it), behavior is unchanged.

    Returns:
        UpdateResult with counts of inserted, promoted, and reset rows.

    Note:
        Caller must already hold the advisory lock for (symbol, granularity).
        This function does not acquire the lock itself — it runs inside the
        caller's transaction and lock scope.
    """
    return _do_update(
        conn,
        symbol,
        granularity,
        from_ts,
        to_ts,
        fetch_status_for_unfilled,
        force_reset_terminal=force_reset_terminal,
        outcome=outcome,
        precomputed_ranges=precomputed_ranges,
    )


def _do_update(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    from_ts: datetime,
    to_ts: datetime,
    fetch_status_for_unfilled: FetchStatus | None,
    *,
    force_reset_terminal: bool,
    outcome: LastAttemptOutcome,
    precomputed_ranges: list[GapRange] | None = None,
) -> UpdateResult:
    terminal_rows_reset = 0

    # Step 1 — snapshot prior rows in window
    prior_rows = _fetch_prior_rows(conn, symbol, granularity, from_ts, to_ts)

    # Step 2 — optional force-reset: clear terminal rows before carry-forward
    if force_reset_terminal:
        terminal_rows_reset = _reset_terminal_rows(conn, symbol, granularity, from_ts, to_ts)
        # Re-snapshot after reset (terminal rows gone)
        prior_rows = _fetch_prior_rows(conn, symbol, granularity, from_ts, to_ts)

    # Carry-forward map: gap_start → max attempt_count for the current fetch_status
    carry_forward: dict[datetime, int] = {}
    if fetch_status_for_unfilled is not None:
        for row in prior_rows:
            if row["fetch_status"] == str(fetch_status_for_unfilled):
                key = row["gap_start"]
                carry_forward[key] = max(carry_forward.get(key, 0), row["attempt_count"])

    # Step 3 — delete intersecting rows
    _delete_intersecting(conn, symbol, granularity, from_ts, to_ts)

    # Step 4 — recompute gap ranges.
    # For daily granularity: compare stored bars against trading sessions
    # (compute_missing_ranges). For minute granularity, the caller (the minute
    # daemon) precomputes coverage-aware missing sessions via
    # compute_missing_minute_sessions (slice 162) and passes them as
    # precomputed_ranges — recomputing here would repeat the same universe-wide
    # scan per symbol. Absent precomputed_ranges, minute falls back to the
    # legacy single-span behavior (e.g. a caller that hasn't adopted the
    # coverage-aware seeder yet).
    if granularity == "minute":
        if precomputed_ranges is not None:
            gap_ranges = precomputed_ranges
        elif fetch_status_for_unfilled is not None:
            gap_ranges = [GapRange(
                symbol=symbol,
                granularity=granularity,
                gap_start_utc=from_ts,
                gap_end_utc=to_ts,
            )]
        else:
            gap_ranges = []
    else:
        gap_ranges = compute_missing_ranges(conn, symbol, granularity, from_ts, to_ts)

    # Step 5 — insert with carried-forward attempt_count
    gaps_inserted = 0
    gaps_promoted_exhausted = 0
    now_utc = datetime.now(tz=timezone.utc)

    if fetch_status_for_unfilled is not None:
        for gap in gap_ranges:
            # Carry forward: find a prior row that overlaps this gap start
            prior_count = _best_prior_count(carry_forward, gap.gap_start_utc)
            attempt_count = prior_count + 1

            status = fetch_status_for_unfilled
            if attempt_count >= MAX_RETRY_COUNT:
                status = FetchStatus.RETRY_EXHAUSTED
                gaps_promoted_exhausted += 1

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data_gaps
                        (symbol, granularity, gap_start, gap_end,
                         fetch_status, last_attempt_ts, attempt_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, granularity, gap_start, gap_end)
                    DO UPDATE SET
                        fetch_status   = EXCLUDED.fetch_status,
                        last_attempt_ts = EXCLUDED.last_attempt_ts,
                        attempt_count  = EXCLUDED.attempt_count
                    """,
                    (
                        symbol,
                        granularity,
                        gap.gap_start_utc,
                        gap.gap_end_utc,
                        str(status),
                        now_utc,
                        attempt_count,
                    ),
                )
            gaps_inserted += 1

    # Step 7 — update acquisition_state
    _update_acquisition_state(conn, symbol, granularity, outcome, now_utc)

    return UpdateResult(
        gaps_inserted=gaps_inserted,
        gaps_promoted_exhausted=gaps_promoted_exhausted,
        terminal_rows_reset=terminal_rows_reset,
    )


# ---------------------------------------------------------------------------
# Internal SQL helpers
# ---------------------------------------------------------------------------


def _fetch_prior_rows(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    from_ts: datetime,
    to_ts: datetime,
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT gap_start, gap_end, fetch_status, attempt_count
              FROM data_gaps
             WHERE symbol = %s
               AND granularity = %s
               AND gap_start >= %s
               AND gap_end <= %s
            """,
            (symbol, granularity, from_ts, to_ts),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _reset_terminal_rows(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    from_ts: datetime,
    to_ts: datetime,
) -> int:
    """Delete PROVIDER_HOLE and RETRY_EXHAUSTED rows in scope; return count."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM data_gaps
             WHERE symbol = %s
               AND granularity = %s
               AND gap_start >= %s
               AND gap_end <= %s
               AND fetch_status IN (%s, %s)
            """,
            (
                symbol,
                granularity,
                from_ts,
                to_ts,
                str(FetchStatus.PROVIDER_HOLE),
                str(FetchStatus.RETRY_EXHAUSTED),
            ),
        )
        return cur.rowcount


def _delete_intersecting(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    from_ts: datetime,
    to_ts: datetime,
) -> None:
    """Delete all data_gaps rows whose [gap_start, gap_end] intersects [from_ts, to_ts]."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM data_gaps
             WHERE symbol = %s
               AND granularity = %s
               AND gap_start >= %s
               AND gap_end <= %s
            """,
            (symbol, granularity, from_ts, to_ts),
        )


def _best_prior_count(carry_forward: dict[datetime, int], gap_start: datetime) -> int:
    """Return the highest attempt_count from prior rows, falling back to 0."""
    return carry_forward.get(gap_start, 0)


def _update_acquisition_state(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    outcome: LastAttemptOutcome,
    now_utc: datetime,
) -> None:
    """Upsert acquisition_state row with last_attempt_ts and last_attempt_outcome."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO acquisition_state
                (symbol, granularity, provider, last_attempt_ts, last_attempt_outcome)
            VALUES (%s, %s, 'eodhd', %s, %s)
            ON CONFLICT (symbol, granularity, provider) DO UPDATE SET
                last_attempt_ts      = EXCLUDED.last_attempt_ts,
                last_attempt_outcome = EXCLUDED.last_attempt_outcome,
                updated_at           = NOW()
            """,
            (symbol, granularity, now_utc, str(outcome)),
        )
