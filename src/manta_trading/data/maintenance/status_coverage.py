"""The single guarded door to the ``data_status`` view (slice 167 D6).

``data_status.bars_summary`` reads the ``minute_coverage`` and ``daily_coverage``
continuous aggregates rather than scanning the raw hypertables per symbol. That
makes every read of this view a *derived* read, and derived reads carry the
failure mode slice 163 hit on production: when a refresh policy stops, coverage
silently freezes and nothing in SQL notices. Documenting the lag bound is what
163 already did, and it did not prevent the incident.

**Every Python reader of ``data_status`` must go through this module.** The
guard cannot live inside the view — ``assert_cagg_fresh`` is Python and
``bars_summary`` is a SQL CTE — so a single guarded accessor is what makes "no
unguarded consumer" enforceable rather than aspirational. Slice 182's serving
API is contractually required to use it too; see the slice 167 design, D6.

Behaviour on a stale verdict is to **report, not refuse** (D3a). Unlike the
daemon's coverage index, which fails safe by skipping work, ``data_status`` is
an operator-facing read: returning nothing would be less useful than returning
coverage plus a clear statement that it is stale. So the accessor returns rows
*and* verdicts, logs at ERROR, and leaves the presentation decision to the
caller. What it must never do is present stale coverage as current.

This module deliberately does **not** remediate. No ``refresh_continuous_
aggregate`` on a read path — a status query must not trigger a heavy write as a
side effect. Catch-up stays with runbook R2.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

import psycopg

from manta_trading.constants import (
    COVERAGE_CONTENT_STALENESS,
    COVERAGE_SOURCE_TABLE,
    DAILY_COVERAGE_VIEW,
    MINUTE_COVERAGE_VIEW,
)
from manta_trading.logging import get_logger
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
    _max_probe,
    _read_statement_timeout,
    _restore_probe_timeout,
    assert_cagg_fresh,
)

_logger = get_logger(__name__)

COVERAGE_VIEWS: tuple[str, ...] = (MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW)
"""The caggs backing ``bars_summary``, asserted on every guarded read."""


@dataclass(frozen=True)
class CoverageFreshness:
    """Freshness of the caggs behind ``data_status``, as one reportable unit.

    Attributes:
        verdicts: One verdict per coverage cagg, in ``COVERAGE_VIEWS`` order.
    """

    verdicts: tuple[FreshnessVerdict, ...]

    @property
    def is_stale(self) -> bool:
        """True when *any* coverage cagg is stale.

        Any one stale cagg makes the whole ``bars_summary`` untrustworthy, since
        a reader cannot tell which rows came from which branch.
        """
        return any(not verdict.is_fresh for verdict in self.verdicts)

    @property
    def stale_verdicts(self) -> tuple[FreshnessVerdict, ...]:
        """Only the verdicts that tripped — what an operator needs to see."""
        return tuple(v for v in self.verdicts if not v.is_fresh)

    def describe(self) -> str:
        """One-line operator summary naming each stale cagg and its lag."""
        if not self.is_stale:
            return "coverage caggs fresh"
        return "; ".join(
            f"{v.view_name}: lag={v.lag} threshold={v.threshold} "
            f"signals={','.join(s.value for s in v.signals)}"
            for v in self.stale_verdicts
        )


CONTENT_EDGE_COLUMN = "last_bucket"
"""The coverage caggs' content timestamp — the column the content-edge check
compares (slice 187 D6).

Not a bucket start. ``minute_coverage``/``daily_coverage`` aggregate
``max(time)`` into ``last_bucket``, so it tracks actual data rather than the
grid, which is precisely why an unaligned comparison against the source is
meaningful here and is not available to the generic guard.
"""


def _content_edge_lag(
    conn: psycopg.Connection[Any], view_name: str
) -> timedelta | None:
    """How far ``view_name``'s content trails its source, or None if unmeasurable.

    Two bounded ``max()`` probes on the connection the caller already holds,
    reusing slice 168's ``_max_probe`` so the statement-timeout discipline is
    shared rather than restated — every statement this issues is capped at
    ``CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT``, and the caller's own setting is
    restored afterwards.

    **No bucket alignment**, which is the entire point: aligning is what makes
    the generic check blind here (``cagg_freshness._raw_max``). Both values are
    raw content timestamps, so the difference is the real lag.

    Returns:
        The lag, or None when either side is empty (nothing ingested, or the
        cagg has never materialized) — an absence of data is not staleness, and
        the generic evaluation already signals the cagg-empty case.
    """
    prior_timeout = _read_statement_timeout(conn)
    try:
        cagg_edge = _max_probe(conn, view_name, CONTENT_EDGE_COLUMN)
        source_edge = _max_probe(conn, COVERAGE_SOURCE_TABLE[view_name], "time")
    except psycopg.Error:
        # Indeterminate, and indeterminate is stale (168 D3) — but the generic
        # evaluation that ran first already carries PROBE_FAILED for a broken
        # connection, so this returns None rather than inventing a second
        # verdict. Logged because a probe that fails here and not there would
        # otherwise be invisible.
        _logger.exception(
            "content-edge probe failed for %s — leaving the generic verdict "
            "to stand",
            view_name,
        )
        return None
    finally:
        _restore_probe_timeout(conn, prior_timeout)

    if cagg_edge is None or source_edge is None:
        return None
    return source_edge - cagg_edge


def _apply_content_edge_check(
    conn: psycopg.Connection[Any], verdict: FreshnessVerdict
) -> FreshnessVerdict:
    """Return ``verdict`` with ``CONTENT_EDGE_TOO_OLD`` appended if it fires.

    A cagg that is already stale for another reason still gets the check, so the
    operator sees every reason at once — the same "collect all signals" rule
    ``_evaluate`` follows. ``is_fresh`` becomes False when this signal fires even
    though the generic bucket check reported fresh, which on production today is
    every time (D5/D6).

    **Except after a failed probe.** ``PROBE_FAILED`` means the generic
    evaluation could not read the connection at all — it timed out or the
    connection is broken. Two more probes down the same connection would at best
    repeat the failure and at worst report a lag derived from a half-broken
    read, and the verdict is already stale on the strongest possible grounds
    (168 D3: indeterminate is stale). Nothing is gained by adding a second
    reason, so the verdict is returned untouched.
    """
    if StalenessSignal.PROBE_FAILED in verdict.signals:
        return verdict

    lag = _content_edge_lag(conn, verdict.view_name)
    if lag is None or lag <= COVERAGE_CONTENT_STALENESS:
        return verdict

    signals = (*verdict.signals, StalenessSignal.CONTENT_EDGE_TOO_OLD)
    return replace(
        verdict,
        is_fresh=False,
        signals=signals,
        # The content lag replaces the bucket lag in the reported verdict: it is
        # the larger and the true one, and reporting lag=0 next to
        # CONTENT_EDGE_TOO_OLD would read as a contradiction to an operator.
        lag=lag,
        threshold=COVERAGE_CONTENT_STALENESS,
        detail=(
            f"{verdict.view_name}: STALE (content lag={lag}, "
            f"threshold={COVERAGE_CONTENT_STALENESS}, "
            f"signals={[s.value for s in signals]}) — "
            f"max({CONTENT_EDGE_COLUMN}) trails "
            f"max(time) on {COVERAGE_SOURCE_TABLE[verdict.view_name]}; the "
            f"bucket-lag check cannot see this (bucket_width="
            f"{verdict.bucket_width})"
        ),
    )


def check_coverage_freshness(
    conn: psycopg.Connection[Any],
    **kwargs: Any,
) -> CoverageFreshness:
    """Assert freshness on both coverage caggs backing ``data_status``.

    Consumes slice 168's ``assert_cagg_fresh``, including its TTL verdict cache
    — which is what keeps repeat reads inside the sub-second NFR. The threshold
    (``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)``) is resolved by that
    helper; this module does not re-derive it.

    Each coverage cagg's source table is supplied explicitly because the helper
    resolves sources from ``GRANULARITY_SOURCE``, which has no entry for the
    coverage caggs (they are not a granularity). See ``COVERAGE_SOURCE_TABLE``
    for why the hierarchical minute cagg is measured against its parent.

    **The content-edge check (187 D6)** is passed as the helper's ``augment``
    hook rather than applied to the returned verdict. That places it inside the
    existing TTL cache — one probe pair per view per TTL window, not one per
    call — and adds no second cache layer. Applying it outside would either
    probe on every cached read or require this module to memoize the result
    itself.

    On production this makes both views report stale until slice 169 repairs the
    refresh policies. That is the correct report of the actual state (167 D3a:
    report, don't refuse) and nothing starts failing, but it is a visible change
    to ``mt data status``, ``/api/v1/health``, and ``/api/v1/status``.

    Args:
        conn:   Open psycopg connection.
        kwargs: Forwarded verbatim to ``assert_cagg_fresh`` (the ``now`` clock
                seam, used by tests). Not part of the production call.

    Returns:
        A ``CoverageFreshness`` carrying one verdict per coverage cagg.
    """
    verdicts = tuple(
        assert_cagg_fresh(
            conn,
            view_name,
            source_table=COVERAGE_SOURCE_TABLE[view_name],
            augment=_apply_content_edge_check,
            **kwargs,
        )
        for view_name in COVERAGE_VIEWS
    )
    freshness = CoverageFreshness(verdicts=verdicts)

    if freshness.is_stale:
        # ERROR, not warning: data_status is reporting coverage that is not
        # current, and the operator reading it cannot tell from the rows alone.
        _logger.error(
            "data_status coverage is STALE — reported bar counts and "
            "first/last timestamps may understate reality: %s",
            freshness.describe(),
        )

    return freshness


DATA_STATUS_RELATION = "data_status"
"""The guarded relation. Defined once here so the enforcement grep in the
slice-167 tests has a single definition to key on, and so no consumer needs to
spell the view name itself."""


def query_data_status(
    conn: psycopg.Connection[Any],
    sql: str,
    params: list[Any] | tuple[Any, ...] | None = None,
    *,
    row_factory: Any = None,
    **freshness_kwargs: Any,
) -> tuple[list[Any], CoverageFreshness]:
    """Run a query against ``data_status`` behind the freshness guard.

    The guard runs *before* the query, so a stale verdict accompanies the very
    rows it describes. Rows are returned regardless (D3a: report, don't refuse)
    — it is the caller's job to surface ``freshness.is_stale``, and never to
    present the rows as current without checking it.

    Args:
        conn:        Open psycopg connection.
        sql:         The query. Must read ``data_status``; callers own their
                     filters and projections.
        params:      Query parameters, passed through to ``execute``.
        row_factory: Optional psycopg row factory (e.g. ``dict_row``).
        freshness_kwargs: Forwarded to ``check_coverage_freshness``.

    Returns:
        ``(rows, freshness)``.
    """
    freshness = check_coverage_freshness(conn, **freshness_kwargs)

    if row_factory is None:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    else:
        with conn.cursor(row_factory=row_factory) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return list(rows), freshness
