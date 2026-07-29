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


def _normalize_covered_day(covered_day: datetime | date) -> date:
    """date_trunc('day', ...) returns a timestamp/timestamptz, not a date —
    normalize so membership checks against ``session.date()`` (a plain date)
    actually match. A datetime key silently never matches a date lookup."""
    return covered_day.date() if isinstance(covered_day, datetime) else covered_day


def _run_coverage_query(
    conn: psycopg.Connection[object],
    sql: str,
    params: tuple[object, ...] | None,
    *,
    caller: str,
    scope: str,
) -> list[tuple[object, ...]] | None:
    """Shared envelope for the minute-coverage queries (code review 165 F001):
    the slice-168 staleness guard, statement timeout, and fail-safe error
    handling live in exactly one place for both builders.

    Slice 168 rationale for the guard: a paused/failing/crashed refresh policy
    freezes the source cagg's leading edge while raw minute_ohlcv keeps
    growing, so every day past the frozen edge reads as uncovered and gets
    re-seeded every cycle — silently, because gap rows land under ON CONFLICT
    DO NOTHING. Refuse rather than seed from a derived read we cannot trust.
    Never auto-remediate (D4) and never fall back to a full-window seed (that
    is the 22-year re-seed slice 162 exists to prevent).

    Args:
        conn:   Open psycopg connection.
        sql:    Coverage query text (cagg name already composed by the caller
                from ``GRANULARITY_SOURCE`` — a constant, never user input).
        params: Bind parameters, or None for a parameterless query.
        caller: Public function name, for log attribution.
        scope:  Human phrase for what is being skipped on failure
                (e.g. "this cycle", "for AAPL").

    Returns:
        Raw fetched rows on success. None when the source cagg is stale
        (slice 168) or the query failed (timeout / operational error) — the
        caller must treat None as "coverage unavailable" and must never fall
        back to a full-window seed on its own initiative.
    """
    cagg = GRANULARITY_SOURCE[Granularity.H4]

    verdict = assert_cagg_fresh(conn, cagg)
    if not verdict.is_fresh:
        _logger.error(
            "%s: source cagg %s is STALE (lag=%s, threshold=%s, signals=%s) "
            "— skipping coverage-aware seeding %s; see runbook R2 to remediate",
            caller,
            cagg,
            verdict.lag,
            verdict.threshold,
            [signal.value for signal in verdict.signals],
            scope,
        )
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SET LOCAL statement_timeout = "
                f"'{MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT}'"
            )
            if params is None:
                cur.execute(sql)
            else:
                cur.execute(sql, params)
            return cast("list[tuple[object, ...]]", cur.fetchall())
    except psycopg.OperationalError:
        _logger.exception(
            "%s: query failed (timeout or operational error) — "
            "skipping coverage-aware seeding %s",
            caller,
            scope,
        )
        return None


def build_minute_coverage_index(
    conn: psycopg.Connection[object],
) -> dict[str, set[date]] | None:
    """Return {symbol: set[covered_day]} from the coarse minute cagg, or None.

    One grouped query over the whole universe, run once per daemon cycle
    before the per-symbol seed loop. Fails safe: on a stale source cagg or a
    statement timeout / operational error mid-scan, logs at ERROR and returns
    None rather than raising — the caller must treat None as "index
    unavailable this cycle" (distinct from an empty dict, which means "no
    symbol covered"). Guard/timeout/error handling shared with
    ``build_symbol_minute_coverage`` via ``_run_coverage_query``.

    Args:
        conn: Open psycopg connection.

    Returns:
        {symbol: set[covered_day]} on success, else None (see above).
    """
    cagg = GRANULARITY_SOURCE[Granularity.H4]
    sql = (
        f"SELECT symbol, date_trunc('day', time_bucket) "  # noqa: S608
        f"FROM {cagg} GROUP BY symbol, date_trunc('day', time_bucket)"
    )
    raw = _run_coverage_query(
        conn, sql, None, caller="build_minute_coverage_index", scope="this cycle"
    )
    if raw is None:
        return None
    # fetchall() on a Connection[object] is untyped; annotate the row so the
    # normalization is actually type-checked.
    rows = cast("list[tuple[str, datetime | date]]", raw)
    index: dict[str, set[date]] = {}
    for symbol, covered_day in rows:
        index.setdefault(symbol, set()).add(_normalize_covered_day(covered_day))
    return index


def build_symbol_minute_coverage(
    conn: psycopg.Connection[object],
    symbol: str,
) -> dict[str, set[date]] | None:
    """Return {symbol: set[covered_day]} for ONE symbol, or None.

    Per-symbol sibling of ``build_minute_coverage_index`` (slice 165): same
    source cagg and the same guard/timeout/fail-safe envelope (shared via
    ``_run_coverage_query``), filtered to a single symbol so single-shot
    operator commands (``run_minute_refetch``) never pay the universe-wide
    scan. Returns the same shape as the universe builder so consumers
    (``compute_missing_minute_sessions``) work unchanged.

    Args:
        conn:   Open psycopg connection.
        symbol: Instrument ticker (bound as a query parameter, never
                interpolated).

    Returns:
        {symbol: set[covered_day]} on success (the set may be empty if the
        symbol has no minute coverage), else None (see universe builder).
    """
    cagg = GRANULARITY_SOURCE[Granularity.H4]
    sql = (
        f"SELECT date_trunc('day', time_bucket) "  # noqa: S608
        f"FROM {cagg} WHERE symbol = %s "
        f"GROUP BY date_trunc('day', time_bucket)"
    )
    raw = _run_coverage_query(
        conn,
        sql,
        (symbol,),
        caller="build_symbol_minute_coverage",
        scope=f"for {symbol}",
    )
    if raw is None:
        return None
    rows = cast("list[tuple[datetime | date]]", raw)
    return {symbol: {_normalize_covered_day(day) for (day,) in rows}}


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
