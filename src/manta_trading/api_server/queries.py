"""SQL shared by more than one route module.

Instrument and gap SQL lives in the route layer by convention (slices 183/184);
this module holds the fragments that would otherwise be written twice. The
OHLCV DB classes are not the place for it — ``instruments`` is not their table.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import psycopg

from manta_trading.constants import (
    CAGG_FRESHNESS_CACHE_TTL,
    DAILY_COVERAGE_VIEW,
    MINUTE_COVERAGE_VIEW,
    CycleGranularity,
)

_SYMBOL_EXISTS_SQL = """
    SELECT 1
    FROM instruments
    WHERE symbol = %s
"""
"""Primary-key seek, no projection: the caller only needs existence."""


def symbol_exists(conn: psycopg.Connection[Any], symbol: str) -> bool:
    """Return whether ``symbol`` is a known instrument (slice 186 D5).

    Deliberately no ``try/except``. This answer *decides a status code* — 404
    versus an empty 200 — and a failed lookup means the server does not know
    which is true. Neither "assume it exists" nor "assume it doesn't" is
    acceptable, so failures propagate to the app-level handlers:
    ``QueryCanceled`` becomes a ``504`` (D10) and any other ``psycopg.Error``
    a sanitized ``500``. Both are retryable and assert nothing about the symbol.
    """
    return conn.execute(_SYMBOL_EXISTS_SQL, (symbol,)).fetchone() is not None


# --- available ranges (slice 187 D2, D3, D7, D8) -----------------------------
#
# Every statement below carries a bound the planner can prune on. That is the
# whole design: an unbounded `MIN/MAX ... WHERE symbol = %s` on daily_ohlcv costs
# 2.5-4.0 s on prod, 96% of it *planning* across 3,371 chunks, and the same query
# with a prunable bound plans in 1.7 ms (D1). If a change here seems to need an
# aggregate with no time predicate, re-read D1 — that is the query this replaced.
#
# The family tag is bound as a parameter rather than written as a SQL literal so
# CycleGranularity stays the single definition site (D7). psycopg adapts a
# StrEnum to its value, and the value round-trips back through
# CycleGranularity(...) below.

_UNIVERSE_EDGES_SQL = f"""
    SELECT %s AS family, MAX(last_bucket AT TIME ZONE 'UTC')::date
    FROM {MINUTE_COVERAGE_VIEW}
    UNION ALL
    SELECT %s, MAX(last_bucket AT TIME ZONE 'UTC')::date
    FROM {DAILY_COVERAGE_VIEW}
"""  # noqa: S608 — view names are module constants, not caller input.
"""Universe-wide leading edge each coverage cagg has materialized (D3).

No per-symbol predicate: this is the *bound* the per-symbol head probe uses, and
it must exist for symbols that have no coverage row at all. Cached under
``CAGG_FRESHNESS_CACHE_TTL`` rather than paid per request — measured at ~32 ms
steady state, 60.9 ms cold.
"""

_SYMBOL_COVERAGE_SQL = f"""
    SELECT %s AS family,
           MIN(first_bucket AT TIME ZONE 'UTC')::date,
           MAX(last_bucket  AT TIME ZONE 'UTC')::date
    FROM {MINUTE_COVERAGE_VIEW} WHERE symbol = %s
    UNION ALL
    SELECT %s,
           MIN(first_bucket AT TIME ZONE 'UTC')::date,
           MAX(last_bucket  AT TIME ZONE 'UTC')::date
    FROM {DAILY_COVERAGE_VIEW}  WHERE symbol = %s
"""  # noqa: S608 — view names are module constants, not caller input.
"""Per-symbol coverage floor, both families in one round trip (D2 statement B).

Reads the caggs, so it is bounded by construction — there is no chunk-exclusion
question. Measured 7-12 ms including for symbols absent from a cagg.

The ``AT TIME ZONE 'UTC'`` casts extend D8's rule to this statement and the
universe-edge one above. D2 writes both as a bare ``::date``, which is correct
only while the session is UTC: ``daily_coverage.first_bucket`` is an exact
``MIN(time)``, so a bar at midnight UTC renders as the *previous* day under a
negative-offset session. Caught by ``test_symbol_ranges_sql`` running against a
test database whose session timezone is not UTC — exactly the dependency D8
removes from the head probe, and it applies identically here.
"""

_SYMBOL_HEAD_SQL = """
    SELECT %s AS family,
           MIN(time_bucket AT TIME ZONE 'UTC')::date,
           MAX(time_bucket AT TIME ZONE 'UTC')::date
    FROM minute_5min_ohlcv WHERE symbol = %s AND time_bucket > %s
    UNION ALL
    SELECT %s,
           MIN(time AT TIME ZONE 'UTC')::date,
           MAX(time AT TIME ZONE 'UTC')::date
    FROM daily_ohlcv       WHERE symbol = %s AND time > %s
"""
"""Per-symbol leading edge past the coverage horizon (D2 statement C).

``minute_5min_ohlcv``, not the 4-hour cagg coverage derives from (D8): it
preserves the pre-187 contract exactly, and its policy's 5-minute ``end_offset``
is the more conservative of the two, so the advertised edge never runs ahead of
what a ``5m`` request can actually return.

Both branches carry a ``time``/``time_bucket`` bound — this is what makes the
read cheap. All four range expressions cast ``AT TIME ZONE 'UTC'`` explicitly
rather than relying on the pool's ``configure`` hook keeping ``timezone='UTC'``
(D8); slice 186 D12b lost real time to a spurious difference from exactly that
dependency.

**The bound must be bound as a timestamptz, not a date.** Measured on prod
2026-08-04, SPY, warm connection, identical SQL and identical instant:

    bind a datetime.date      3,100 ms
    bind an aware datetime        7 ms

A ``date`` parameter adapts to PostgreSQL ``date``, and ``timestamptz > date``
resolves through a timezone-dependent conversion the planner cannot use for
chunk exclusion — so it plans across all 3,371 chunks and reintroduces exactly
the D1 cost this statement exists to avoid. The plan still shows "Chunks
excluded during startup", which is why the fast *execution* time hides it; the
cost is in planning. ``_as_bound`` below is what keeps this correct, and
``test_head_bound_is_timestamptz_typed`` is what keeps it from regressing.
"""


def fetch_universe_edges(
    conn: psycopg.Connection[Any],
) -> dict[CycleGranularity, date | None]:
    """Each coverage cagg's universe-wide leading edge (D3).

    Returns:
        One entry per ``CycleGranularity`` member, always both — a cagg with no
        rows maps to ``None``, which callers must read as "no bound available"
        rather than "no data". ``fetch_symbol_head`` skips a family with no
        edge rather than falling back to an unbounded scan.
    """
    edges: dict[CycleGranularity, date | None] = {
        CycleGranularity.MINUTE: None,
        CycleGranularity.DAILY: None,
    }
    rows = conn.execute(
        _UNIVERSE_EDGES_SQL,
        (CycleGranularity.MINUTE, CycleGranularity.DAILY),
    ).fetchall()
    for family, edge in rows:
        edges[CycleGranularity(family)] = edge
    return edges


def _utcnow() -> datetime:
    """Wall clock, isolated so tests can substitute it via monkeypatch.

    Callers taking a ``now`` seam must default it to ``None`` and resolve it
    here at call time rather than binding this function as a default argument
    value — see ``UniverseEdgeCache.get`` and ``cagg_freshness._now`` for why.
    """
    return datetime.now(UTC)


class UniverseEdgeCache:
    """TTL cache for the universe-wide coverage edges (D3).

    The edges cost ~32 ms steady state (60.9 ms cold) and are identical for
    every symbol, so paying for them per request would dominate a read the rest
    of which is ~20 ms. One instance lives on ``app.state``; ``deps.get_universe_edges``
    is the accessor routes use.

    **TTL is ``CAGG_FRESHNESS_CACHE_TTL``, reused deliberately.** The coverage
    refresh policies fire hourly, so a 60 s window cannot mask an edge movement,
    and this is already the project's answer to "how long may a cagg-derived
    fact be cached" (168 D6). A second constant for the same 60 s policy would
    be two things to keep in step.

    **Thread safety is required, not defensive.** The route reads this from a
    worker thread inside ``run_in_executor``, so concurrent requests touch it
    from several threads at once. The lock is held across the fetch so a cold
    cache under concurrency issues exactly one query rather than one per waiting
    thread — the fetch is ~32 ms and the alternative is a thundering herd
    against a pool of 8.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._edges: dict[CycleGranularity, date | None] | None = None
        self._fetched_at: datetime | None = None

    def get(
        self,
        conn: psycopg.Connection[Any],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> dict[CycleGranularity, date | None]:
        """Return the cached edges, refreshing them if the TTL has expired.

        Args:
            conn: Connection used only on a miss.
            now:  Clock seam, so expiry is testable without sleeping. Defaults
                  to ``None`` and resolves to :func:`_utcnow` at call time —
                  **not** to a default argument value. A default is evaluated
                  once at import, so ``monkeypatch.setattr(queries, "_utcnow",
                  ...)`` would rebind the module attribute while the captured
                  default kept pointing at the original, a freeze that silently
                  does nothing. Same rule, and the same reason, as
                  ``cagg_freshness._now``.
        """
        clock = _utcnow if now is None else now
        with self._lock:
            current = clock()
            if (
                self._edges is not None
                and self._fetched_at is not None
                and current - self._fetched_at < CAGG_FRESHNESS_CACHE_TTL
            ):
                return self._edges

            edges = fetch_universe_edges(conn)
            self._edges = edges
            self._fetched_at = current
            return edges

    def clear(self) -> None:
        """Drop the cached value. For tests and for the load tier, which resets
        it between measured runs so a cold read is actually cold."""
        with self._lock:
            self._edges = None
            self._fetched_at = None


def fetch_symbol_coverage(
    conn: psycopg.Connection[Any], symbol: str
) -> dict[CycleGranularity, tuple[date | None, date | None]]:
    """The symbol's coverage floor per family (D2 statement B).

    Returns:
        A mapping with an entry per family the caggs returned a row for. A
        symbol absent from both caggs yields entries of ``(None, None)``, not
        an exception and not an empty mapping — the aggregate always produces a
        row, and the merge treats all-``None`` as "omit this family".
    """
    rows = conn.execute(
        _SYMBOL_COVERAGE_SQL,
        (CycleGranularity.MINUTE, symbol, CycleGranularity.DAILY, symbol),
    ).fetchall()
    return {CycleGranularity(family): (start, end) for family, start, end in rows}


def _as_bound(edge: date) -> datetime:
    """Adapt a coverage edge to a ``timestamptz`` bound at UTC midnight.

    Not cosmetic: binding the ``date`` directly costs 3,100 ms against 7 ms for
    the equivalent aware ``datetime`` (see ``_SYMBOL_HEAD_SQL``). Midnight UTC
    is the same instant the ``date`` would have compared as under a UTC session,
    so the *result* is unchanged — only the plan is.
    """
    return datetime(edge.year, edge.month, edge.day, tzinfo=UTC)


def fetch_symbol_head(
    conn: psycopg.Connection[Any],
    symbol: str,
    edges: dict[CycleGranularity, date | None],
) -> dict[CycleGranularity, tuple[date | None, date | None]]:
    """The symbol's data past each family's coverage horizon (D2 statement C).

    Args:
        edges: Per-family bounds from :func:`fetch_universe_edges`. A family
            whose edge is ``None`` is **skipped entirely** — there is no
            unbounded fallback, because the unbounded form of this query is the
            2.5-4.0 s statement this slice exists to remove (D1).

    Returns:
        A mapping with an entry per probed family. ``(None, None)`` means the
        symbol has no data past the horizon — the delisted case, which measures
        7-12 ms and does not degrade to a scan.
    """
    minute_edge = edges.get(CycleGranularity.MINUTE)
    daily_edge = edges.get(CycleGranularity.DAILY)
    if minute_edge is None or daily_edge is None:
        return _fetch_head_partial(conn, symbol, minute_edge, daily_edge)

    rows = conn.execute(
        _SYMBOL_HEAD_SQL,
        (
            CycleGranularity.MINUTE,
            symbol,
            _as_bound(minute_edge),
            CycleGranularity.DAILY,
            symbol,
            _as_bound(daily_edge),
        ),
    ).fetchall()
    return {CycleGranularity(family): (start, end) for family, start, end in rows}


_MINUTE_HEAD_ONLY_SQL = """
    SELECT %s AS family,
           MIN(time_bucket AT TIME ZONE 'UTC')::date,
           MAX(time_bucket AT TIME ZONE 'UTC')::date
    FROM minute_5min_ohlcv WHERE symbol = %s AND time_bucket > %s
"""

_DAILY_HEAD_ONLY_SQL = """
    SELECT %s AS family,
           MIN(time AT TIME ZONE 'UTC')::date,
           MAX(time AT TIME ZONE 'UTC')::date
    FROM daily_ohlcv WHERE symbol = %s AND time > %s
"""


def _fetch_head_partial(
    conn: psycopg.Connection[Any],
    symbol: str,
    minute_edge: date | None,
    daily_edge: date | None,
) -> dict[CycleGranularity, tuple[date | None, date | None]]:
    """Head probe for the case where only one family has a usable bound.

    Separate statements rather than a UNION with a ``WHERE false`` branch: an
    unbounded branch is exactly what must never reach the planner, and a
    conditional predicate would leave that possibility one edit away. In
    practice both caggs are populated and this path is not taken; it exists so
    an empty cagg degrades to "one family unavailable" instead of to a scan.
    """
    result: dict[CycleGranularity, tuple[date | None, date | None]] = {}
    if minute_edge is not None:
        rows = conn.execute(
            _MINUTE_HEAD_ONLY_SQL,
            (CycleGranularity.MINUTE, symbol, _as_bound(minute_edge)),
        ).fetchall()
        result.update({CycleGranularity(f): (s, e) for f, s, e in rows})
    if daily_edge is not None:
        rows = conn.execute(
            _DAILY_HEAD_ONLY_SQL,
            (CycleGranularity.DAILY, symbol, _as_bound(daily_edge)),
        ).fetchall()
        result.update({CycleGranularity(f): (s, e) for f, s, e in rows})
    return result


def merge_available_ranges(
    coverage: dict[CycleGranularity, tuple[date | None, date | None]],
    head: dict[CycleGranularity, tuple[date | None, date | None]],
) -> dict[CycleGranularity, tuple[date, date]]:
    """Combine the coverage floor and the head probe into one range per family.

    The ``COALESCE`` order is the design (D2):

    - ``start = COALESCE(coverage_start, head_start)`` — coverage holds the true
      historical floor, but a symbol whose data begins *after* the coverage
      horizon has no coverage row at all, so the head probe must be able to
      supply it. That is why coverage is preferred rather than taken
      unconditionally.
    - ``end = COALESCE(head_end, coverage_end)`` — the head probe holds the true
      leading edge, and falls back to coverage for a symbol whose data stops
      before the horizon (delisted, or ingest stopped).

    A family is omitted when both sides resolve to ``None``, matching the
    pre-187 contract for a symbol with no data in that family.

    Pure: no I/O and no connection, so every D2 case is unit-testable without a
    database.

    Returns:
        ``{family: (start, end)}`` for each family with data. A family is also
        omitted if only one endpoint resolves — a half-open range is not
        representable in ``AvailableRange`` and would be a worse answer than
        silence.
    """
    merged: dict[CycleGranularity, tuple[date, date]] = {}
    for family in CycleGranularity:
        coverage_start, coverage_end = coverage.get(family, (None, None))
        head_start, head_end = head.get(family, (None, None))

        start = coverage_start if coverage_start is not None else head_start
        end = head_end if head_end is not None else coverage_end
        if start is None or end is None:
            continue
        merged[family] = (start, end)
    return merged
