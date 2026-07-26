"""Cagg freshness assertion for derived-data readers (slice 168).

A TimescaleDB refresh policy only reconsiders the last ``start_offset`` of data,
so any interruption longer than that leaves a hole the policy **never heals** on
resume. Slice 163's Phase D hit this in production: job 1003
(``minute_4hour_ohlcv`` refresh) was paused for restructuring and left paused.
The daemon's coverage index reads that exact cagg, so its leading edge froze
while raw ``minute_ohlcv`` kept growing, and ~349 of 4,198 symbols were
re-seeded every cycle for four days. It was silent — gap rows land under
``ON CONFLICT DO NOTHING``, so nothing errored.

163 added a ``preflight()`` guard covering exactly one path: maintenance tooling
refusing to repair a cagg while the coverage-index cagg's refresh is paused. The
other causes — crashed job, policy failing every fire, out-of-band ``alter_job``,
restart mid-maintenance — never pass through maintenance tooling. This module
puts the check in the **reader** path instead.

Design invariants (slice 168 D1-D6):

- Indeterminate freshness is **stale** (D3). A probe that cannot answer refuses.
- Never auto-remediate (D4) — no ``refresh_continuous_aggregate`` in a read path.
- The threshold is ``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)``. The
  ceiling is required: the daily caggs' 21/90/270-day offsets would otherwise
  let a daily cagg stalled 100 days pass every ``start_offset``-relative check.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import cast

import psycopg

from manta_trading.constants import (
    CAGG_BASE_GRANULARITY,
    CAGG_FRESHNESS_CACHE_TTL,
    CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT,
    GRANULARITY_SOURCE,
    MAX_COVERAGE_SOURCE_STALENESS,
    Granularity,
)
from manta_trading.logging import get_logger

logger = get_logger(__name__)

# Reverse of GRANULARITY_SOURCE: view name -> granularity. Built once from the
# single source of truth rather than restated, so adding a granularity there
# extends freshness coverage automatically.
_VIEW_GRANULARITY: dict[str, Granularity] = {
    source: granularity for granularity, source in GRANULARITY_SOURCE.items()
}

# The catalog proc_name identifying a continuous aggregate's refresh policy.
# Same value cagg_repair.py filters on; defined here so this module has no
# import-time dependency on the maintenance sweep.
_PROC_REFRESH = "policy_refresh_continuous_aggregate"

# TimescaleDB stores a refresh policy's start_offset inside jobs.config (jsonb)
# as an interval *string* ("1 day", "270 days", "04:00:00") — there is no
# start_offset column. Casting in SQL means psycopg hands back a timedelta
# rather than this module re-implementing PostgreSQL interval parsing.
_JOB_SQL = (
    "SELECT j.job_id, j.scheduled, "
    "(j.config ->> 'start_offset')::interval AS start_offset, "
    "s.last_run_status, s.last_successful_finish "
    "FROM timescaledb_information.jobs j "
    "LEFT JOIN timescaledb_information.job_stats s USING (job_id) "
    "WHERE j.hypertable_name = %s AND j.proc_name = %s"
)

# TimescaleDB's own spelling of a successful job run in job_stats.
_STATUS_SUCCESS = "Success"

# Number of columns _JOB_SQL selects; guards the row unpack.
_JOB_ROW_FIELDS = 5


class StalenessSignal(StrEnum):
    """Why a cagg was judged stale. Every dispatch and log site uses these
    members — never a bare string.

    The first four are the D1 signals, OR'd: any one alone is sufficient to
    refuse. The last two are the indeterminate causes (D3/F001), which are
    treated as stale rather than as an exemption.
    """

    LAG_EXCEEDS_THRESHOLD = "LAG_EXCEEDS_THRESHOLD"
    """raw ``max(time)`` minus cagg ``max(time_bucket)`` exceeds the threshold."""

    NOT_SCHEDULED = "NOT_SCHEDULED"
    """The refresh policy exists but is paused (``scheduled = false``) — the
    exact shape of the 163 incident."""

    LAST_SUCCESS_TOO_OLD = "LAST_SUCCESS_TOO_OLD"
    """``now() - last_successful_finish`` exceeds the threshold: the policy is
    scheduled but has not actually completed within the budget."""

    LAST_RUN_FAILED = "LAST_RUN_FAILED"
    """``last_run_status <> 'Success'`` — the policy is firing and failing, which
    a ``scheduled``-only check reports as healthy."""

    NO_JOB_ROW = "NO_JOB_ROW"
    """No refresh policy exists for the view at all. Never self-healing — the
    strongest form of the incident, not an exemption from it."""

    PROBE_FAILED = "PROBE_FAILED"
    """A catalog read or edge probe raised (timeout, connection loss). Freshness
    is indeterminate, and indeterminate is stale (D3)."""


@dataclass(frozen=True)
class FreshnessVerdict:
    """The result of one freshness evaluation for a single cagg view.

    Attributes:
        view_name: The cagg view the verdict describes.
        is_fresh:  True only when no signal fired.
        signals:   Every signal that fired, not just the first — the ERROR log
                   names all of them so an operator sees the full picture.
        lag:       Measured raw-to-cagg lag, or None when it could not be
                   measured (an empty table or a failed probe).
        threshold: The resolved ``min(start_offset, ceiling)`` the lag was
                   judged against, or None when it could not be resolved.
        detail:    Human-readable summary for the ERROR log.
    """

    view_name: str
    is_fresh: bool
    signals: tuple[StalenessSignal, ...]
    lag: timedelta | None
    threshold: timedelta | None
    detail: str


@dataclass(frozen=True)
class _JobRow:
    """One cagg refresh policy, as read from the TimescaleDB job catalog."""

    job_id: int
    scheduled: bool
    start_offset: timedelta | None
    last_run_status: str | None
    last_successful_finish: datetime | None


def _set_probe_timeout(cur: psycopg.Cursor[object]) -> None:
    """Bound every statement this module issues on the caller's connection.

    ``SET LOCAL`` so the bound is scoped to the caller's transaction and never
    leaks into the reader's own subsequent queries (the coverage-index scan has
    its own, much larger, budget). Called before *every* probe — including the
    paths that early-return — so no query this module issues can run unbounded.
    """
    cur.execute(
        f"SET LOCAL statement_timeout = '{CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT}'"
    )


def _read_refresh_job(
    conn: psycopg.Connection[object], view_name: str
) -> _JobRow | None:
    """The view's refresh policy from the job catalog, or None if it has none.

    ``timescaledb_information.jobs.hypertable_name`` carries the *view* name for
    a cagg's refresh policy (the ``cagg_repair.py`` precedent). One round trip
    supplies all four D1 catalog inputs. ``view_name`` is bound, never
    interpolated.

    Returns:
        The parsed row, or None when no refresh policy matches — the caller
        turns that into ``NO_JOB_ROW``.
    """
    with conn.cursor() as cur:
        _set_probe_timeout(cur)
        cur.execute(_JOB_SQL, (view_name, _PROC_REFRESH))
        row = cast(
            "tuple[int, bool, timedelta | None, str | None, datetime | None] | None",
            cur.fetchone(),
        )
    if row is None or len(row) != _JOB_ROW_FIELDS:
        # No refresh policy for this view. A short row cannot happen against a
        # real cursor for this fixed SELECT list, but treating it as "no row"
        # keeps the failure mode a refusal rather than an unpack ValueError
        # that would escape as a caller-visible crash.
        return None
    job_id, scheduled, start_offset, last_run_status, last_successful_finish = row
    return _JobRow(
        job_id=int(job_id),
        scheduled=bool(scheduled),
        start_offset=start_offset,
        last_run_status=last_run_status,
        last_successful_finish=last_successful_finish,
    )


def _cagg_max(conn: psycopg.Connection[object], view_name: str) -> datetime | None:
    """The cagg's leading edge: ``max(time_bucket)``, or None if empty."""
    return _max_probe(conn, view_name, "time_bucket")


def _raw_max(conn: psycopg.Connection[object], source_table: str) -> datetime | None:
    """The raw hypertable's leading edge: ``max(time)``, or None if empty."""
    return _max_probe(conn, source_table, "time")


def _max_probe(
    conn: psycopg.Connection[object], relation: str, column: str
) -> datetime | None:
    """One bounded ``max()`` edge probe.

    ``relation`` is never caller input: it arrives already resolved through
    ``GRANULARITY_SOURCE`` (see ``_resolve_source_table``), so the identifier
    cannot be attacker-controlled. Identifiers cannot be bound parameters in
    PostgreSQL, which is why this is interpolated rather than passed as %s.
    """
    with conn.cursor() as cur:
        _set_probe_timeout(cur)
        cur.execute(f"SELECT max({column}) FROM {relation}")  # noqa: S608
        row = cast("tuple[datetime | None] | None", cur.fetchone())
    if row is None:
        return None
    return row[0]


def _resolve_source_table(view_name: str) -> str:
    """The raw hypertable a cagg view derives from.

    Granularity-agnostic by construction (D5): both sides come from
    ``GRANULARITY_SOURCE``, so slice 167 consumes this unchanged for the daily
    caggs. Minute caggs derive from ``minute_ohlcv``, daily caggs from
    ``daily_ohlcv`` — the two base hypertables, which are the entries mapping to
    themselves.

    Raises:
        ValueError: ``view_name`` is not a known cagg view. This is a caller
            bug and must not be absorbed into a staleness refusal (F001).
    """
    granularity = _VIEW_GRANULARITY.get(view_name)
    if granularity is None:
        raise ValueError(
            f"{view_name!r} is not a known continuous aggregate view "
            f"(expected one of {sorted(_VIEW_GRANULARITY)})"
        )
    base = GRANULARITY_SOURCE[CAGG_BASE_GRANULARITY[granularity]]
    if view_name == base:
        raise ValueError(
            f"{view_name!r} is a base hypertable, not a continuous "
            f"aggregate — nothing to assert freshness against"
        )
    return base


def _resolve_threshold(start_offset: timedelta | None) -> timedelta:
    """The staleness budget: ``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)``.

    The ceiling is load-bearing, not defensive. A refresh policy only reconsiders
    the last ``start_offset`` of data, so ``start_offset`` alone is the natural
    budget — but the daily caggs use 21/90/270-day offsets, which would let a
    daily cagg stalled 100 days pass every ``start_offset``-relative check.

    ``start_offset is None`` (a policy configured without one) falls back to the
    ceiling alone rather than to "no bound".
    """
    if start_offset is None:
        return MAX_COVERAGE_SOURCE_STALENESS
    return min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)


def _now() -> datetime:
    """Wall clock, isolated so tests can substitute it via monkeypatch."""
    return datetime.now(timezone.utc)


# Process-local verdict cache (D6), keyed by view name only: {view: (at, verdict)}.
# Not shared across processes and not persisted — a fresh process re-probes.
_VERDICT_CACHE: dict[str, tuple[datetime, FreshnessVerdict]] = {}


def reset_freshness_cache() -> None:
    """Drop all cached verdicts. For tests and for long-lived processes that
    need a forced re-probe; not part of the normal read path."""
    _VERDICT_CACHE.clear()


def assert_cagg_fresh(
    conn: psycopg.Connection[object],
    view_name: str,
    *,
    now: Callable[[], datetime] = _now,
) -> FreshnessVerdict:
    """Assert a continuous aggregate is fresh enough to read from.

    The four D1 signals are OR'd; any one refuses. Indeterminate freshness (no
    refresh policy, a failed probe) is treated as stale — never as a pass (D3).
    This function never remediates (D4): detecting staleness is a read-path
    concern, repairing it is runbook R2's.

    Verdicts are memoized per view name for ``CAGG_FRESHNESS_CACHE_TTL``, so a
    caller that reads several times inside one cycle pays the ~1 s probe cost
    once. **Stale verdicts cache on exactly the same terms as fresh ones** — the
    cache can never turn a refusal into a pass. The TTL is two orders of
    magnitude below ``MAX_COVERAGE_SOURCE_STALENESS``, so a cached verdict
    cannot mask a lag the uncached check would have caught.

    **Not for maintenance decisions.** ``cagg_repair.preflight()`` remains the
    uncached, always-probing guard for anything that mutates a cagg; a cached
    verdict is fine for deciding whether to trust a read, not for deciding
    whether it is safe to restructure.

    Args:
        conn:      Open psycopg connection. Every statement this issues is
                   bounded by ``CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT``.
        view_name: The cagg view to assert, e.g. ``minute_4hour_ohlcv``.
        now:       Clock seam; overridden in tests to exercise TTL expiry
                   without sleeping.

    Returns:
        A FreshnessVerdict. Callers must check ``is_fresh`` and refuse to use
        the derived data when it is False.

    Raises:
        ValueError: ``view_name`` is not a known cagg view (a caller bug).
    """
    cached = _VERDICT_CACHE.get(view_name)
    current_time = now()
    if cached is not None and current_time - cached[0] < CAGG_FRESHNESS_CACHE_TTL:
        return cached[1]

    verdict = _evaluate(conn, view_name)
    _VERDICT_CACHE[view_name] = (current_time, verdict)
    return verdict


def _evaluate(conn: psycopg.Connection[object], view_name: str) -> FreshnessVerdict:
    """Uncached freshness evaluation: the four D1 signals, OR'd.

    Every signal that fires is collected, not just the first — a policy can be
    both paused and lagging, and the operator needs to see both. Any one signal
    alone is sufficient to refuse.

    Raises:
        ValueError: ``view_name`` is not a known cagg view (a caller bug, which
            must not be absorbed into a refusal — F001).
    """
    # Resolved before the try: a bad view name is a caller bug and must
    # propagate as ValueError rather than being reported as staleness.
    source_table = _resolve_source_table(view_name)

    try:
        job = _read_refresh_job(conn, view_name)
        cagg_max = _cagg_max(conn, view_name)
        raw_max = _raw_max(conn, source_table)
    except psycopg.Error:
        # D3/F001: a probe timeout or connection loss leaves freshness
        # indeterminate, and indeterminate is stale. Trapping is correct here
        # because the reader's contract is "refuse and skip", not "raise" —
        # propagating would turn a degraded read into a caller-visible crash.
        logger.exception(
            "cagg freshness probe failed for %s — treating as stale", view_name
        )
        return FreshnessVerdict(
            view_name=view_name,
            is_fresh=False,
            signals=(StalenessSignal.PROBE_FAILED,),
            lag=None,
            threshold=None,
            detail=f"{view_name}: freshness probe failed (see traceback above)",
        )

    if job is None:
        # A cagg with no refresh policy never self-heals — the strongest form of
        # the 163 incident, not an exemption from it.
        return FreshnessVerdict(
            view_name=view_name,
            is_fresh=False,
            signals=(StalenessSignal.NO_JOB_ROW,),
            lag=None,
            threshold=None,
            detail=f"{view_name}: no refresh policy found in the job catalog",
        )

    threshold = _resolve_threshold(job.start_offset)
    signals: list[StalenessSignal] = []

    lag: timedelta | None = None
    if raw_max is not None and cagg_max is not None:
        lag = raw_max - cagg_max
        if lag > threshold:
            signals.append(StalenessSignal.LAG_EXCEEDS_THRESHOLD)

    if not job.scheduled:
        signals.append(StalenessSignal.NOT_SCHEDULED)

    if job.last_successful_finish is None:
        # Scheduled but never completed once: the same operational hole as a
        # success that has aged out of the budget.
        signals.append(StalenessSignal.LAST_SUCCESS_TOO_OLD)
    elif _now() - job.last_successful_finish > threshold:
        signals.append(StalenessSignal.LAST_SUCCESS_TOO_OLD)

    if job.last_run_status is not None and job.last_run_status != _STATUS_SUCCESS:
        signals.append(StalenessSignal.LAST_RUN_FAILED)

    is_fresh = not signals
    detail = (
        f"{view_name}: fresh (lag={lag}, threshold={threshold})"
        if is_fresh
        else (
            f"{view_name}: STALE (lag={lag}, threshold={threshold}, "
            f"job_id={job.job_id}, signals={[s.value for s in signals]})"
        )
    )
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=is_fresh,
        signals=tuple(signals),
        lag=lag,
        threshold=threshold,
        detail=detail,
    )
