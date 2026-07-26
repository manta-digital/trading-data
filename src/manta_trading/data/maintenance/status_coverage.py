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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from manta_trading.constants import (
    COVERAGE_SOURCE_TABLE,
    DAILY_COVERAGE_VIEW,
    MINUTE_COVERAGE_VIEW,
)
from manta_trading.logging import get_logger
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    assert_cagg_fresh,
)

if TYPE_CHECKING:
    import psycopg

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


def check_coverage_freshness(
    conn: psycopg.Connection[Any],
    **kwargs: Any,
) -> CoverageFreshness:
    """Assert freshness on both coverage caggs backing ``data_status``.

    Consumes slice 168's ``assert_cagg_fresh`` unchanged, including its TTL
    verdict cache — which is what keeps repeat reads inside the sub-second NFR.
    The threshold (``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)``) is
    resolved by that helper; this module does not re-derive it.

    Each coverage cagg's source table is supplied explicitly because the helper
    resolves sources from ``GRANULARITY_SOURCE``, which has no entry for the
    coverage caggs (they are not a granularity). See ``COVERAGE_SOURCE_TABLE``
    for why the hierarchical minute cagg is measured against its parent.

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
