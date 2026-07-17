"""Coverage-aware minute gap-seeding (slice 162).

Builds a universe-wide day-granularity coverage index from the coarse
``minute_4hour_ohlcv`` cagg and diffs it against the trading-session calendar
per symbol, so minute gap-seeding recreates only genuinely-missing sessions
instead of a single full-history span.

See slice-design §"The Fix — Batch Coverage Index + Day-Granularity Diff".
"""

from __future__ import annotations

from datetime import date, datetime

import psycopg

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT,
    Granularity,
)
from manta_trading.data.gaps.compute_missing_ranges import (
    GapRange,
    clamp_to_lifecycle,
    fetch_sessions,
    group_sessions_into_ranges,
)
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


def build_minute_coverage_index(
    conn: "psycopg.Connection[object]",
) -> dict[str, set[date]] | None:
    """Return {symbol: set[covered_day]} from the coarse minute cagg, or None.

    One grouped query over the whole universe, run once per daemon cycle
    before the per-symbol seed loop. Fails safe: on a statement timeout or
    other operational error mid-scan, logs at ERROR and returns None rather
    than raising — the caller must treat None as "index unavailable this
    cycle" (distinct from an empty dict, which means "no symbol covered").

    Args:
        conn: Open psycopg connection.

    Returns:
        {symbol: set[covered_day]} on success. None if the query failed
        (timeout / operational error) — coverage-aware seeding must be
        skipped for this cycle; never fall back to a full-window seed.
    """
    cagg = GRANULARITY_SOURCE[Granularity.H4]
    sql = (
        f"SELECT symbol, date_trunc('day', time_bucket) "  # noqa: S608
        f"FROM {cagg} GROUP BY symbol, date_trunc('day', time_bucket)"
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SET LOCAL statement_timeout = '{MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT}'"
            )
            cur.execute(sql)
            index: dict[str, set[date]] = {}
            for symbol, covered_day in cur.fetchall():
                index.setdefault(symbol, set()).add(covered_day)
            return index
    except psycopg.OperationalError:
        _logger.exception(
            "build_minute_coverage_index: query failed (timeout or operational "
            "error) — skipping coverage-aware seeding this cycle"
        )
        return None


def compute_missing_minute_sessions(
    conn: "psycopg.Connection[object]",
    symbol: str,
    coverage_index: dict[str, set[date]],
    from_ts: datetime,
    to_ts: datetime,
) -> list[GapRange]:
    """Return GapRanges for trading sessions missing from the coverage index.

    Diffs at day granularity: a session is "covered" if its day appears in
    ``coverage_index[symbol]``, regardless of per-minute bar presence.

    Args:
        conn:           Open psycopg connection.
        symbol:         Instrument ticker.
        coverage_index: Result of build_minute_coverage_index (must not be None
                        — caller is responsible for the None fail-safe branch).
        from_ts:        Window start (UTC, inclusive) — typically history_start.
        to_ts:          Window end (UTC, inclusive) — typically today.

    Returns:
        Ordered list of GapRange objects (earliest first). Empty list if the
        symbol is fully covered or has no lifecycle dates.
    """
    clamped_from, clamped_to = clamp_to_lifecycle(conn, symbol, from_ts, to_ts)
    if clamped_from is None:
        return []

    sessions = fetch_sessions(conn, symbol, clamped_from, clamped_to)
    if not sessions:
        return []

    covered_days = coverage_index.get(symbol, set())
    missing = [s for s in sessions if s.date() not in covered_days]
    if not missing:
        return []

    return group_sessions_into_ranges(symbol, "minute", missing, sessions)
