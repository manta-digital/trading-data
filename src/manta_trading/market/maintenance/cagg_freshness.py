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
from psycopg import sql

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
#
# last_successful_finish is '-infinity' for a policy that has been created but
# has never completed a run. psycopg cannot load that into a datetime — it
# raises DataError mid-fetch, which would surface as PROBE_FAILED and refuse
# reads on every freshly-created cagg. Normalize it to NULL in SQL, which is
# the same "never succeeded" state the column uses before job_stats has a row.
_JOB_SQL = (
    "SELECT j.job_id, j.scheduled, "
    "(j.config ->> 'start_offset')::interval AS start_offset, "
    "(j.config ->> 'end_offset')::interval AS end_offset, "
    "s.last_run_status, "
    "nullif(s.last_successful_finish, '-infinity'::timestamptz) "
    "  AS last_successful_finish "
    "FROM timescaledb_information.jobs j "
    "LEFT JOIN timescaledb_information.job_stats s USING (job_id) "
    "WHERE j.hypertable_name = %s AND j.proc_name = %s"
)

# A cagg's bucket width, from the TimescaleDB catalog. Stored as an interval
# *string* and may be variable-width ("1 mon", "3 mons"), which is why the raw
# edge is bucketed by PostgreSQL rather than by arithmetic in Python.
_BUCKET_WIDTH_SQL = (
    "SELECT bf.bucket_width "
    "FROM _timescaledb_catalog.continuous_agg ca "
    "JOIN _timescaledb_catalog.continuous_aggs_bucket_function bf "
    "  USING (mat_hypertable_id) "
    "WHERE ca.user_view_name = %s"
)

# TimescaleDB's own spelling of a successful job run in job_stats.
_STATUS_SUCCESS = "Success"

# Number of columns _JOB_SQL selects; guards the row unpack.
_JOB_ROW_FIELDS = 6


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

    CONTENT_EDGE_TOO_OLD = "CONTENT_EDGE_TOO_OLD"
    """The cagg's ``max(last_bucket)`` trails its source's ``max(time)`` by more
    than ``COVERAGE_CONTENT_STALENESS`` (slice 187 D6).

    Fires **only** from ``status_coverage.check_coverage_freshness``, never from
    the generic ``_evaluate`` above. It measures content lag with no bucket
    alignment, which is exactly what ``LAG_EXCEEDS_THRESHOLD`` structurally
    cannot see for a wide-bucket cagg (see ``_raw_max``'s detection floor).
    ``minute_coverage``/``daily_coverage`` carry a ``last_bucket`` column that is
    a content timestamp rather than a bucket start, which is what makes the
    unaligned comparison meaningful for them and unavailable in general.
    """


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
        bucket_width: The cagg's bucket width as a PostgreSQL interval string,
                   or None when it was not reached (an early return) or the
                   view is not a cagg. Exposed because it *is* the resolution
                   limit of ``lag`` — see ``_raw_max``: no lag smaller than one
                   bucket width is observable, so a caller judging a wide-bucket
                   cagg needs this to know what the verdict cannot tell it
                   (slice 187 D6).
    """

    view_name: str
    is_fresh: bool
    signals: tuple[StalenessSignal, ...]
    lag: timedelta | None
    threshold: timedelta | None
    detail: str
    bucket_width: str | None = None


@dataclass(frozen=True)
class _JobRow:
    """One cagg refresh policy, as read from the TimescaleDB job catalog."""

    job_id: int
    scheduled: bool
    start_offset: timedelta | None
    end_offset: timedelta | None
    last_run_status: str | None
    last_successful_finish: datetime | None


def _set_probe_timeout(cur: psycopg.Cursor[object]) -> None:
    """Bound every statement this module issues on the caller's connection.

    Plain ``SET``, deliberately **not** ``SET LOCAL``. ``SET LOCAL`` is scoped
    to the enclosing transaction, and on an autocommit connection — which is how
    the maintenance paths and the integration fixtures connect — each statement
    is its own transaction, so ``SET LOCAL`` is discarded before the next
    statement runs and ``statement_timeout`` stays 0 (unlimited). Verified
    against PG 17.7 on 2026-07-26: ``SET LOCAL`` then ``SHOW`` returns ``0``
    under autocommit, and a ``pg_sleep(2)`` under a 100 ms ``SET LOCAL`` runs to
    completion. That would leave every probe unbounded, which is the exact
    failure D3 requires us to prevent.

    Called before *every* probe — including the paths that early-return — so no
    query this module issues can run unbounded. ``_restore_probe_timeout``
    puts the caller's own setting back afterwards so the reader's much larger
    budget is not clamped to the probe's.

    """
    cur.execute(f"SET statement_timeout = '{CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT}'")


def _read_statement_timeout(conn: psycopg.Connection[object]) -> str | None:
    """The session's current ``statement_timeout``, for later restoration.

    Read rather than assumed: a caller that set its own session-level timeout
    before invoking the guard must get *that* value back, not the
    postgresql.conf default (review F002). Returns None when it cannot be
    read, in which case the restore falls back to ``DEFAULT`` — the previous
    behavior, and no worse than it.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            row = cast("tuple[str] | None", cur.fetchone())
        return row[0] if row is not None else None
    except psycopg.Error:
        # Never let bookkeeping turn a readable cagg into a refusal; the probes
        # below will surface a genuinely broken connection on their own.
        logger.exception("failed to read statement_timeout before freshness probe")
        return None


def _restore_probe_timeout(
    conn: psycopg.Connection[object], prior: str | None = None
) -> None:
    """Put ``statement_timeout`` back the way the caller had it.

    ``prior`` is the value ``_set_probe_timeout`` observed. Restoring to
    ``DEFAULT`` instead would silently discard a session-level timeout the
    caller had set before calling the guard — today no reader does, but the
    guard sits on a read path where one plausibly would (review F002).

    Best-effort: the verdict is already decided by the time this runs, and a
    failure here must not turn a completed evaluation into an exception.
    """
    try:
        with conn.cursor() as cur:
            if prior is None:
                cur.execute("SET statement_timeout = DEFAULT")
            else:
                cur.execute(f"SET statement_timeout = '{prior}'")
    except psycopg.Error:
        # The connection is already broken (this runs after a probe failure in
        # the failure path); the caller's next statement will surface it.
        logger.exception("failed to restore statement_timeout after freshness probe")


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
            "tuple[int, bool, timedelta | None, timedelta | None, str | None, "
            "datetime | None] | None",
            cur.fetchone(),
        )
    if row is None or len(row) != _JOB_ROW_FIELDS:
        # No refresh policy for this view. A short row cannot happen against a
        # real cursor for this fixed SELECT list, but treating it as "no row"
        # keeps the failure mode a refusal rather than an unpack ValueError
        # that would escape as a caller-visible crash.
        return None
    (
        job_id,
        scheduled,
        start_offset,
        end_offset,
        last_run_status,
        last_successful_finish,
    ) = row
    return _JobRow(
        job_id=int(job_id),
        scheduled=bool(scheduled),
        start_offset=start_offset,
        end_offset=end_offset,
        last_run_status=last_run_status,
        last_successful_finish=last_successful_finish,
    )


def _bucket_width(conn: psycopg.Connection[object], view_name: str) -> str | None:
    """The cagg's bucket width as a PostgreSQL interval string, from the
    catalog. ``None`` if the view is not a continuous aggregate.

    Returned as a string, not a timedelta, because month- and quarter-width
    buckets ("1 mon", "3 mons") have no fixed length — only PostgreSQL's
    ``time_bucket`` can align a timestamp to them correctly.
    """
    with conn.cursor() as cur:
        _set_probe_timeout(cur)
        cur.execute(_BUCKET_WIDTH_SQL, (view_name,))
        row = cast("tuple[str] | None", cur.fetchone())
    if row is None:
        return None
    return row[0]


def _cagg_max(conn: psycopg.Connection[object], view_name: str) -> datetime | None:
    """The cagg's leading edge: ``max(time_bucket)``, or None if empty."""
    return _max_probe(conn, view_name, "time_bucket")


def _raw_max(
    conn: psycopg.Connection[object],
    source_table: str,
    bucket_width: str | None = None,
) -> datetime | None:
    """The raw hypertable's leading edge, aligned to the cagg's bucket grid.

    Both sides of the lag comparison must be bucket *starts*. ``time_bucket``
    on a cagg is the start of its window, so comparing it to a raw timestamp
    would report the bucket width itself as lag: measured on prod 2026-07-26,
    a healthy ``daily_quarterly_ohlcv`` sat 72 days "behind" raw purely
    because its newest bucket had a quarter still to run. Bucketing the raw
    edge to the same grid cancels that structural offset, so the threshold
    stays the plain ``min(start_offset, ceiling)`` D2 specifies instead of
    growing a bucket-width term.

    ``bucket_width`` is a PostgreSQL interval string bound as a parameter and
    cast in SQL, so variable-width month/quarter buckets align correctly
    without any Python-side interval arithmetic. When it is None (the source
    table is not a cagg's base, or the width could not be read) the probe
    degrades to a plain ``max(time)``.

    **The detection floor this creates, stated explicitly (slice 187 D6).**
    Bucketing the raw edge onto the cagg's own grid means both sides of the lag
    comparison are bucket *starts*, so **no lag smaller than one bucket width
    can ever be observed** — a cagg whose newest materialized bucket is the same
    bucket the raw edge falls into always reports ``lag=0``, however far behind
    inside that bucket it actually is. That is correct and deliberate for narrow
    buckets (the 4 h, 1 day, and 3 month caggs this was built for), where one
    bucket is well inside the staleness budget.

    It is **vacuous** for a cagg whose bucket is wide relative to its threshold.
    ``minute_coverage`` and ``daily_coverage`` bucket at
    ``COVERAGE_BUCKET_INTERVAL`` (365 days) against a threshold near one day: on
    prod 2026-08-04 both returned ``is_fresh=True, lag=0`` while
    ``daily_coverage``'s content was 52 days behind raw. The generic guard has
    no general way to see inside a bucket, so the fix does not live here — it
    lives in the coverage-specific layer, which has a content timestamp
    (``last_bucket``) to compare instead of a bucket start. See
    ``status_coverage.check_coverage_freshness`` and
    ``StalenessSignal.CONTENT_EDGE_TOO_OLD``.

    ``FreshnessVerdict.bucket_width`` carries this width to callers so the floor
    is inspectable rather than implicit, and
    ``test_cagg_freshness``'s detection-floor test pins it: it fails if this
    alignment step is changed without acknowledging the consequence.
    """
    if bucket_width is None:
        return _max_probe(conn, source_table, "time")
    with conn.cursor() as cur:
        _set_probe_timeout(cur)
        # source_table is composed as an identifier, not interpolated: it is
        # reachable from the public assert_cagg_fresh seam (review F001).
        cur.execute(
            sql.SQL(
                "SELECT time_bucket(%s::interval, max(time)) FROM {relation}"
            ).format(relation=sql.Identifier(source_table)),
            (bucket_width,),
        )
        row = cast("tuple[datetime | None] | None", cur.fetchone())
    if row is None:
        return None
    return row[0]


def _max_probe(
    conn: psycopg.Connection[object], relation: str, column: str
) -> datetime | None:
    """One bounded ``max()`` edge probe.

    Identifiers cannot be bound parameters in PostgreSQL, so ``relation`` and
    ``column`` are composed with ``psycopg.sql.Identifier``, which quotes and
    escapes them. Production callers pass values resolved through
    ``GRANULARITY_SOURCE``/``COVERAGE_SOURCE_TABLE``, but ``source_table`` is a
    public parameter on ``assert_cagg_fresh``, so the safety cannot rest on
    every caller being well-behaved (review F001): composing the identifier
    makes a hostile value a lookup failure rather than injected SQL.
    """
    with conn.cursor() as cur:
        _set_probe_timeout(cur)
        cur.execute(
            sql.SQL("SELECT max({column}) FROM {relation}").format(
                column=sql.Identifier(column),
                relation=sql.Identifier(relation),
            )
        )
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


def _resolve_threshold(
    start_offset: timedelta | None, end_offset: timedelta | None = None
) -> timedelta:
    """The staleness budget: ``min(start_offset, ceiling) + end_offset``.

    The ceiling is load-bearing, not defensive. A refresh policy only reconsiders
    the last ``start_offset`` of data, so ``start_offset`` alone is the natural
    budget — but the daily caggs use 21/90/270-day offsets, which would let a
    daily cagg stalled 100 days pass every ``start_offset``-relative check.

    ``end_offset`` is added because a policy deliberately refuses to materialize
    the most recent ``end_offset`` of data, so that much lag is configured, not
    stale. Bucket width is *not* added — the raw edge is bucketed to the cagg's
    own grid instead (see ``_raw_max``), which cancels that term exactly rather
    than budgeting for it. Verified on prod 2026-07-26:
    ``daily_monthly_ohlcv``'s newest bucket is one month behind raw precisely
    because its ``end_offset`` is 30 days.

    ``start_offset is None`` (a policy configured without one) falls back to the
    ceiling alone rather than to "no bound".
    """
    base = (
        MAX_COVERAGE_SOURCE_STALENESS
        if start_offset is None
        else min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)
    )
    return base if end_offset is None else base + end_offset


def _now() -> datetime:
    """Wall clock, isolated so tests can substitute it via monkeypatch.

    Callers taking a ``now`` seam must default it to ``None`` and resolve it
    through :func:`_resolve_clock` rather than binding ``_now`` as a default
    argument value. A default argument is evaluated once at import time, so
    ``monkeypatch.setattr(cagg_freshness, "_now", ...)`` would rebind the
    module attribute while the captured default kept pointing at the original
    function — a freeze that silently does nothing and lets time-dependent
    signals fire off the real clock.
    """
    return datetime.now(timezone.utc)


def _resolve_clock(now: Callable[[], datetime] | None) -> Callable[[], datetime]:
    """Resolve a clock seam at call time so monkeypatching ``_now`` works."""
    return _now if now is None else now


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
    now: Callable[[], datetime] | None = None,
    source_table: str | None = None,
    augment: Callable[
        [psycopg.Connection[object], FreshnessVerdict], FreshnessVerdict
    ]
    | None = None,
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
        now:       Clock seam covering **all** time-dependent logic — both TTL
                   expiry here and the ``LAST_SUCCESS_TOO_OLD`` comparison in
                   the evaluation it wraps. Overridden in tests to exercise
                   expiry and staleness without sleeping.
        source_table: Raw hypertable seam. Production callers omit it and the
                   source is resolved from ``GRANULARITY_SOURCE``; the
                   integration tests pass a scratch table so staleness can be
                   induced without touching a production cagg or its policy.
        augment:   Optional post-evaluation hook, ``(conn, verdict) -> verdict``,
                   for a caller that can measure something this generic guard
                   cannot. It runs **only on a cache miss**, so whatever it
                   probes is cached on exactly the same terms as the rest of the
                   verdict and repeat reads stay inside the NFR — the reason it
                   is a seam here rather than a wrapper around the call.
                   ``status_coverage`` uses it for the content-edge check the
                   one-bucket detection floor makes invisible (187 D6). It must
                   only ever *add* staleness: a hook that flips ``is_fresh`` to
                   True would defeat D3, and nothing in this module re-checks it.

    Returns:
        A FreshnessVerdict. Callers must check ``is_fresh`` and refuse to use
        the derived data when it is False.

    Raises:
        ValueError: ``view_name`` is not a known cagg view (a caller bug).
    """
    clock = _resolve_clock(now)
    cached = _VERDICT_CACHE.get(view_name)
    current_time = clock()
    if cached is not None and current_time - cached[0] < CAGG_FRESHNESS_CACHE_TTL:
        return cached[1]

    verdict = _evaluate(conn, view_name, source_table=source_table, now=clock)
    if augment is not None:
        verdict = augment(conn, verdict)
    _VERDICT_CACHE[view_name] = (current_time, verdict)
    return verdict


def _evaluate(
    conn: psycopg.Connection[object],
    view_name: str,
    *,
    source_table: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> FreshnessVerdict:
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
    resolved_source = source_table or _resolve_source_table(view_name)

    # Read once, before any probe overwrites it, so both restore paths put back
    # the caller's own setting rather than the server default (review F002).
    prior_timeout = _read_statement_timeout(conn)

    try:
        job = _read_refresh_job(conn, view_name)
        bucket_width = _bucket_width(conn, view_name)
        cagg_max = _cagg_max(conn, view_name)
        raw_max = _raw_max(conn, resolved_source, bucket_width)
    except psycopg.Error:
        _restore_probe_timeout(conn, prior_timeout)
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

    _restore_probe_timeout(conn, prior_timeout)

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
            bucket_width=bucket_width,
        )

    threshold = _resolve_threshold(job.start_offset, job.end_offset)
    signals: list[StalenessSignal] = []

    lag: timedelta | None = None
    if raw_max is None:
        # Empty raw table: nothing has been ingested, so there is no lag to
        # measure and no derived data worth reading. Fresh-by-default here would
        # mean "trust a cagg over a source we could not read".
        signals.append(StalenessSignal.PROBE_FAILED)
    elif cagg_max is None:
        # Raw has rows but the cagg has none: the cagg has never materialized
        # anything, which is maximal lag, not an absence of it.
        signals.append(StalenessSignal.LAG_EXCEEDS_THRESHOLD)
    else:
        lag = raw_max - cagg_max
        if lag > threshold:
            signals.append(StalenessSignal.LAG_EXCEEDS_THRESHOLD)

    if not job.scheduled:
        signals.append(StalenessSignal.NOT_SCHEDULED)

    # A policy that has never fired reports last_successful_finish = NULL and
    # last_run_status = NULL (verified against TimescaleDB 2.23 on 2026-07-26).
    # That is a policy created moments ago on an already-materialized cagg, not
    # a stalled one — the cold-start case, and the shape every freshly-built
    # cagg passes through. Judging it stale would refuse reads on a healthy new
    # cagg. Its actual currency is still covered: the lag signal above measures
    # the edges directly and does not depend on job history.
    if (
        job.last_successful_finish is not None
        and _resolve_clock(now)() - job.last_successful_finish > threshold
    ):
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
        bucket_width=bucket_width,
    )
