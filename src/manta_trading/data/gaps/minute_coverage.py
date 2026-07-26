"""Coverage-aware minute gap-seeding (slice 162).

Builds a universe-wide day-granularity coverage index from the coarse
``minute_4hour_ohlcv`` cagg and diffs it against the trading-session calendar
per symbol, so minute gap-seeding recreates only genuinely-missing sessions
instead of a single full-history span.

See slice-design §"The Fix — Batch Coverage Index + Day-Granularity Diff".
"""

from __future__ import annotations

from datetime import date, datetime
from typing import cast

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
from manta_trading.market.maintenance.cagg_freshness import assert_cagg_fresh

_logger = get_logger(__name__)


def build_minute_coverage_index(
    conn: psycopg.Connection[object],
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
        {symbol: set[covered_day]} on success. None if the source cagg is stale
        (slice 168) or the query failed (timeout / operational error) —
        coverage-aware seeding must be skipped for this cycle; never fall back
        to a full-window seed.
    """
    cagg = GRANULARITY_SOURCE[Granularity.H4]

    # Slice 168: a paused/failing/crashed refresh policy freezes this cagg's
    # leading edge while raw minute_ohlcv keeps growing, so every day past the
    # frozen edge reads as uncovered and gets re-seeded every cycle — silently,
    # because gap rows land under ON CONFLICT DO NOTHING. Refuse rather than
    # seed from a derived read we cannot trust. Never auto-remediate (D4) and
    # never fall back to a full-window seed (that is the 22-year re-seed 162
    # exists to prevent).
    verdict = assert_cagg_fresh(conn, cagg)
    if not verdict.is_fresh:
        _logger.error(
            "build_minute_coverage_index: source cagg %s is STALE "
            "(lag=%s, threshold=%s, signals=%s) — skipping coverage-aware "
            "seeding this cycle; see runbook R2 to remediate",
            cagg,
            verdict.lag,
            verdict.threshold,
            [signal.value for signal in verdict.signals],
        )
        return None

    sql = (
        f"SELECT symbol, date_trunc('day', time_bucket) "  # noqa: S608
        f"FROM {cagg} GROUP BY symbol, date_trunc('day', time_bucket)"
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SET LOCAL statement_timeout = "
                f"'{MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT}'"
            )
            cur.execute(sql)
            index: dict[str, set[date]] = {}
            # fetchall() on a Connection[object] is untyped; annotate the row so
            # the normalization below is actually type-checked.  The date_trunc
            # column arrives as datetime, and a mismatch here is invisible at
            # runtime (a datetime key simply never matches a date lookup).
            rows = cast("list[tuple[str, datetime | date]]", cur.fetchall())
            for symbol, covered_day in rows:
                # date_trunc('day', ...) returns a timestamp/timestamptz, not a
                # date — normalize so membership checks against session.date()
                # (a plain date) actually match.
                day = (
                    covered_day.date()
                    if isinstance(covered_day, datetime)
                    else covered_day
                )
                index.setdefault(symbol, set()).add(day)
            return index
    except psycopg.OperationalError:
        _logger.exception(
            "build_minute_coverage_index: query failed (timeout or operational "
            "error) — skipping coverage-aware seeding this cycle"
        )
        return None


def compute_missing_minute_sessions(
    conn: psycopg.Connection[object],
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
    if clamped_from is None or clamped_to is None:
        return []

    sessions = fetch_sessions(conn, symbol, clamped_from, clamped_to)
    if not sessions:
        return []

    covered_days = coverage_index.get(symbol, set())
    missing = [s for s in sessions if s.date() not in covered_days]
    if not missing:
        return []

    return group_sessions_into_ranges(symbol, "minute", missing, sessions)
