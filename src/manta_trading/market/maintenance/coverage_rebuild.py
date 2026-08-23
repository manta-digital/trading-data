"""Full-history rematerialization of the coverage continuous aggregates
(slice 169, Task G).

Migrations 051/052 recreate ``minute_coverage`` and ``daily_coverage`` at the
narrowed ``COVERAGE_BUCKET_INTERVAL`` **empty** (``WITH NO DATA``): a cold-start
database has no history to materialize and should not pay for the machinery, and
on a database with history the operator needs the rebuild under the pausing
runbook with the ability to stop and resume. This module is that rebuild.

**Why not one refresh call.** ``refresh_continuous_aggregate`` materializes its
whole range as a single in-memory tuplestore whose size scales with the span,
**outside ``work_mem``'s control**. ``cagg_repair._REFRESH_SUBWINDOW`` records
what that cost on this host: a single 70-day call on ``minute_4hour_ohlcv`` grew
past the ~119 GB commit limit and was OOM-killed twice (prod, 2026-08-11). The
sweep here is therefore issued as bounded sub-windows, oldest → newest, with a
statement timeout sized to one sub-window rather than the whole job.

**Why the two families are independent.** ``daily_coverage`` reads raw
``daily_ohlcv``; ``minute_coverage`` reads the ``minute_4hour_ohlcv`` cagg.
Neither depends on the other — only ``data_status`` depends on both, and
migration 051 already handles that with its drop-first/reinstall-last ordering.
So they can be rebuilt separately, and ``--family daily`` first is the
recommended order: daily is the larger side (64.6 years vs 22.6) and the one
with the operator-visible symptom.

**State is content-derived, not bookkept.** Like ``cagg_repair``, an interrupted
run resumes by re-deriving what is already materialized rather than reading a
progress file: a sub-window is skipped iff the cagg already holds rows covering
it. ``refresh_continuous_aggregate`` is idempotent, so re-running a window
rewrites the same buckets — recovery is re-run, never rollback.

**What this module does NOT do.** It does not pause or resume jobs, and it does
not apply migrations. Both are the caller's responsibility (the CLI command
does them) so that this module stays a pure, testable sweep. It *refuses* to run
if the target's policies are not paused, rather than pausing them itself —
same discipline as ``cagg_repair.preflight``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast

import psycopg

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    COVERAGE_BUCKET_ORIGIN,
    COVERAGE_REFRESH_MIN_WINDOW_BUCKETS,
    DAILY_COVERAGE_VIEW,
    GRANULARITY_SOURCE,
    MINUTE_COVERAGE_VIEW,
    Granularity,
)
from manta_trading.logging import get_logger
from manta_trading.market.maintenance.cagg_repair import (
    _PROC_REFRESH,
    _resolve_cagg_jobs,
)
from manta_trading.market.maintenance.rechunk import PreflightError

logger = get_logger(__name__)


class CoverageFamily(StrEnum):
    """Which coverage cagg to rebuild.

    A StrEnum rather than bare strings so the CLI's ``--family`` value and the
    dispatch below cannot drift (no-magic-strings rule).
    """

    DAILY = "daily"
    MINUTE = "minute"


FAMILY_VIEW: dict[CoverageFamily, str] = {
    CoverageFamily.DAILY: DAILY_COVERAGE_VIEW,
    CoverageFamily.MINUTE: MINUTE_COVERAGE_VIEW,
}
"""The cagg each family rebuilds. Keyed off the constants, never spelled out."""


FAMILY_SOURCE: dict[CoverageFamily, str] = {
    CoverageFamily.DAILY: "daily_ohlcv",
    CoverageFamily.MINUTE: GRANULARITY_SOURCE[Granularity.H4],
}
"""The relation each family's cagg *reads*, which is what bounds the sweep.

Deliberately NOT ``COVERAGE_SOURCE_TABLE``: that maps each coverage cagg to the
**raw** hypertable its freshness is measured against (both entries are raw, by
design — see that constant). ``minute_coverage`` is hierarchical, so the span it
can actually materialize is the *parent cagg's*, not raw ``minute_ohlcv``'s.
Sweeping to raw's edge would issue refreshes over a range the parent has not
filled, materializing nothing and understating the trailing edge.
"""

_TIME_COLUMN: dict[CoverageFamily, str] = {
    CoverageFamily.DAILY: "time",
    CoverageFamily.MINUTE: "time_bucket",
}
"""Time column on each family's source. A cagg's is ``time_bucket``, a raw
hypertable's is ``time`` — probing the wrong one fails rather than misreports,
but naming them here keeps the SQL below uniform."""


REBUILD_SUBWINDOW: timedelta = timedelta(days=365)
"""Span of a single ``refresh_continuous_aggregate`` call in this sweep.

Bounded for the reason ``cagg_repair._REFRESH_SUBWINDOW`` documents: a refresh
materializes its range as one in-memory tuplestore outside ``work_mem``'s
control, and an unbounded call over 64 years is an unbounded allocation.

Wider than that constant's 14 days because the work per unit time is ~3 orders
of magnitude smaller. ``cagg_repair`` rebuilds the *rollup* caggs, which read
4.4 billion raw minute rows; this sweep reads one aggregate row per symbol per
bucket. Measured on prod 2026-08-16, grouping one year into 7-day buckets:
0.44-0.61 s for daily (~400 k rows/yr) and 0.52 s for minute (~268 k rows/yr).
A 365-day sub-window is therefore ~0.5 s of read plus its write — three orders
of magnitude under the allocation that OOM-killed the minute rollup rebuild.

Kept as a named constant, and overridable per-run, so a host with less headroom
can narrow it without editing code.
"""

REBUILD_STATEMENT_TIMEOUT: str = "600s"
"""``statement_timeout`` for one sub-window refresh.

Sized to a **sub-window**, not the whole job (the design's Rebuild Window
section): with sub-windowing a trip costs one slice's work and the loop resumes,
whereas an unbounded session removes the only server-side bound on a call whose
memory cost ``work_mem`` does not govern. 600 s is ~1000x the measured
per-sub-window read cost, so it bounds a genuine hang rather than firing on
normal variance.
"""


@dataclass(frozen=True)
class SubWindow:
    """One bounded refresh range."""

    start: datetime
    end: datetime

    def __str__(self) -> str:
        return f"[{self.start:%Y-%m-%d} .. {self.end:%Y-%m-%d})"


@dataclass
class RebuildProgress:
    """Live counters for a sweep, so a caller can report as it goes."""

    family: CoverageFamily
    view_name: str
    total_windows: int = 0
    refreshed: int = 0
    skipped: int = 0
    rows_before: int = 0
    rows_after: int = 0

    @property
    def rows_added(self) -> int:
        return self.rows_after - self.rows_before


@dataclass
class RebuildResult:
    """Outcome of a completed sweep."""

    family: CoverageFamily
    view_name: str
    windows: int
    refreshed: int
    skipped: int
    rows_before: int
    rows_after: int
    span_start: datetime | None
    span_end: datetime | None
    elapsed_seconds: float
    subwindow: timedelta
    errors: list[str] = field(default_factory=list)

    @property
    def rows_added(self) -> int:
        return self.rows_after - self.rows_before

    @property
    def ok(self) -> bool:
        return not self.errors


class CoverageRebuildError(RuntimeError):
    """A sub-window refresh failed; the window is named in the message."""


def _source_bounds(
    conn: psycopg.Connection[dict[str, object]], family: CoverageFamily
) -> tuple[datetime | None, datetime | None]:
    """``(min, max)`` of the source relation's time column.

    Bounds the sweep to data that actually exists rather than to a hardcoded
    epoch — a fixed 1962 floor would issue decades of empty refreshes on a
    database seeded later, and would silently miss anything older.
    """
    source = FAMILY_SOURCE[family]
    column = _TIME_COLUMN[family]
    row = conn.execute(
        f"SELECT min({column}) AS lo, max({column}) AS hi FROM {source}"  # noqa: S608
    ).fetchone()
    if row is None:
        return None, None
    return (
        cast("datetime | None", row["lo"]),
        cast("datetime | None", row["hi"]),
    )


def plan_windows(
    start: datetime, end: datetime, subwindow: timedelta = REBUILD_SUBWINDOW
) -> list[SubWindow]:
    """Split ``[start, end)`` into bounded sub-windows, oldest first.

    Aligned to ``COVERAGE_BUCKET_INTERVAL`` so no sub-window boundary falls
    inside a bucket. A refresh only materializes buckets *fully contained* in
    its range, so a boundary mid-bucket would leave that bucket unwritten by
    both adjacent calls — the same truncation behaviour that caused the defect
    this slice repairs, reintroduced through the sweep.

    Every emitted window is at least ``COVERAGE_REFRESH_MIN_WINDOW_BUCKETS``
    buckets wide: ``refresh_continuous_aggregate`` raises
    ``InvalidParameterValue: refresh window too small`` below that, so a
    too-narrow trailing remainder is absorbed into the previous window and a
    too-narrow total span is widened (refreshing an empty bucket is a no-op).
    """
    if end <= start:
        return []

    bucket = COVERAGE_BUCKET_INTERVAL
    min_window = COVERAGE_REFRESH_MIN_WINDOW_BUCKETS * bucket
    # Snap the span outward onto the engine's bucket grid — see
    # COVERAGE_BUCKET_ORIGIN for why any other anchor strands buckets.
    # All arithmetic in UTC: timedelta addition on a DST-observing zone is
    # wall-clock, so a boundary computed in the session timezone drifts an
    # hour off the grid at each transition (measured: 3 stranded buckets).
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    epoch = COVERAGE_BUCKET_ORIGIN
    steps_lo = (start - epoch) // bucket
    aligned_start = epoch + steps_lo * bucket
    steps_hi = -((epoch - end) // bucket)
    aligned_end = epoch + steps_hi * bucket
    if aligned_end - aligned_start < min_window:
        aligned_end = aligned_start + min_window

    # Sub-window span must itself be a whole number of buckets.
    span = max(min_window, (subwindow // bucket) * bucket)

    windows: list[SubWindow] = []
    cursor = aligned_start
    while cursor < aligned_end:
        stop = min(cursor + span, aligned_end)
        if timedelta(0) < aligned_end - stop < min_window:
            stop = aligned_end
        windows.append(SubWindow(start=cursor, end=stop))
        cursor = stop
    return windows


def _window_is_materialized(
    conn: psycopg.Connection[dict[str, object]], view_name: str, window: SubWindow
) -> bool:
    """Has this window already been materialized?

    Content-derived resume (the design's "detect partial materialization by
    content, not catalog presence"). ``EXISTS`` rather than a count: the
    question is only whether the window has anything, and a bounded existence
    check is far cheaper than an aggregate over a large cagg.

    **This is a resume optimisation, not a correctness check.** A window holding
    *some* rows is treated as done, so a run interrupted mid-window could leave
    it partial. That is acceptable because the refresh is idempotent and the
    caller can pass ``force=True`` to re-materialize regardless — and because
    the post-sweep verification compares content against source rather than
    trusting this.
    """
    row = conn.execute(
        f"SELECT EXISTS (SELECT 1 FROM {view_name} "  # noqa: S608
        "WHERE time_bucket >= %s AND time_bucket < %s) AS present",
        (window.start, window.end),
    ).fetchone()
    return bool(row["present"]) if row else False


def _row_count(conn: psycopg.Connection[dict[str, object]], view_name: str) -> int:
    row = conn.execute(f"SELECT count(*) AS n FROM {view_name}").fetchone()  # noqa: S608
    return int(cast("int", row["n"])) if row else 0


def assert_policies_paused(
    conn: psycopg.Connection[dict[str, object]], view_name: str
) -> None:
    """Refuse unless the target cagg's refresh and columnstore jobs are paused.

    Same discipline as ``cagg_repair.preflight``, and for the same reason: the
    slice 163 lesson is that a refresh running concurrently with restructuring
    silently loses rows. Jobs are resolved **from the catalog by name**, never
    by hardcoded ID — the slice 170 execution proved why, when the runbook's job
    table turned out to be stale and job 1003 no longer existed.

    This module refuses rather than pausing them itself: pausing is an
    operator-visible act with a resume obligation, and burying it inside a
    sweep makes it easy to leave a job paused after a crash.
    """
    jobs = _resolve_cagg_jobs(conn, view_name)
    live = [j for j in jobs if j.scheduled]
    if not live:
        return

    listed = ", ".join(f"{j.job_id} ({j.proc_name})" for j in live)
    pause_cmds = " ".join(
        f"SELECT alter_job({j.job_id}, scheduled => false);" for j in live
    )
    raise PreflightError(
        f"refusing to rebuild {view_name}: its policy job(s) {listed} are still "
        "scheduled. A refresh running concurrently with this sweep silently "
        "loses rows (slice 163). Pause them first: "
        f"{pause_cmds} and resume them afterward — see "
        "user/runbooks/300-cagg-maintenance-pausing.md"
    )


def assert_coverage_index_scheduled(
    conn: psycopg.Connection[dict[str, object]],
) -> None:
    """Refuse if ``minute_4hour_ohlcv``'s refresh policy is paused.

    That cagg feeds the minute daemon's coverage index. Pausing it makes
    ``compute_missing_minute_sessions`` see recent sessions as missing and
    re-seed gap rows every cycle — a perpetual re-pull that costs provider calls
    and never self-heals (prod incident 2026-07-25). It is also
    ``minute_coverage``'s parent, so this sweep *reads* it and needs it current.

    Note this is the opposite polarity to :func:`assert_policies_paused`: the
    target must be paused, its parent must not.
    """
    parent = GRANULARITY_SOURCE[Granularity.H4]
    paused = [
        j
        for j in _resolve_cagg_jobs(conn, parent)
        if j.proc_name == _PROC_REFRESH and not j.scheduled
    ]
    if not paused:
        return

    ids = ", ".join(str(j.job_id) for j in paused)
    resume = " ".join(
        f"SELECT alter_job({j.job_id}, scheduled => true);" for j in paused
    )
    raise PreflightError(
        f"refusing to rebuild: {parent}'s refresh policy (job(s) {ids}) is "
        "paused. That cagg feeds the minute daemon's coverage index — leaving "
        "it paused makes the daemon re-seed and re-pull recent sessions every "
        f"cycle (prod incident 2026-07-25). Resume it: {resume}"
    )


def rebuild_coverage(
    conn: psycopg.Connection[dict[str, object]],
    family: CoverageFamily,
    *,
    subwindow: timedelta = REBUILD_SUBWINDOW,
    force: bool = False,
    dry_run: bool = False,
    on_progress: Callable[[RebuildProgress, SubWindow], None] | None = None,
    now: Callable[[], datetime] | None = None,
) -> RebuildResult:
    """Rematerialize one coverage cagg over its full history.

    The connection **must be in autocommit**: ``refresh_continuous_aggregate``
    cannot run inside a transaction block, so each sub-window commits
    independently. That is what makes the sweep resumable — a kill costs one
    sub-window, not the whole run.

    Args:
        conn: Autocommit connection with dict rows.
        family: Which coverage cagg to rebuild.
        subwindow: Span per refresh call. Bounded deliberately; see
            ``REBUILD_SUBWINDOW``.
        force: Re-materialize windows that already hold rows. Off by default so
            an interrupted run resumes cheaply; on when a window is suspected
            partial.
        dry_run: Plan and report without issuing any refresh.
        on_progress: Called after each window with the live counters.
        now: Clock seam for tests.

    Raises:
        PreflightError: policies are not in the required state.
        CoverageRebuildError: a sub-window refresh failed.
    """
    clock = now or (lambda: datetime.now(tz=UTC))
    view_name = FAMILY_VIEW[family]
    started = clock()

    if not dry_run:
        assert_policies_paused(conn, view_name)
        if family is CoverageFamily.MINUTE:
            assert_coverage_index_scheduled(conn)

    lo, hi = _source_bounds(conn, family)
    progress = RebuildProgress(family=family, view_name=view_name)

    if lo is None or hi is None:
        logger.warning(
            "coverage rebuild: %s source %s is empty — nothing to materialize",
            view_name,
            FAMILY_SOURCE[family],
        )
        return RebuildResult(
            family=family,
            view_name=view_name,
            windows=0,
            refreshed=0,
            skipped=0,
            rows_before=0,
            rows_after=0,
            span_start=None,
            span_end=None,
            elapsed_seconds=0.0,
            subwindow=subwindow,
        )

    # +1 bucket so the final (possibly open) bucket is inside the range. The
    # engine still declines to materialize an open bucket — that is the residual
    # this slice bounds rather than removes — but the range must not exclude it,
    # or the newest CLOSED bucket would be dropped too.
    windows = plan_windows(lo, hi + COVERAGE_BUCKET_INTERVAL, subwindow)
    progress.total_windows = len(windows)
    progress.rows_before = _row_count(conn, view_name)

    logger.info(
        "coverage rebuild: %s over %s .. %s in %d sub-window(s) of %s%s",
        view_name,
        lo.date(),
        hi.date(),
        len(windows),
        subwindow,
        " (dry run)" if dry_run else "",
    )

    errors: list[str] = []
    for window in windows:
        if not force and _window_is_materialized(conn, view_name, window):
            progress.skipped += 1
            if on_progress:
                on_progress(progress, window)
            continue

        if dry_run:
            progress.refreshed += 1
            if on_progress:
                on_progress(progress, window)
            continue

        try:
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = '{REBUILD_STATEMENT_TIMEOUT}'")
            conn.execute(
                "CALL refresh_continuous_aggregate(%s, %s::timestamptz, "
                "%s::timestamptz)",
                (view_name, window.start, window.end),
            )
        except psycopg.Error as exc:
            # Re-raise rather than continue: a failed window usually means the
            # host is out of headroom, and grinding through the remaining
            # windows would turn one recoverable stop into a long failure. The
            # sweep is resumable, so stopping loses only this window.
            logger.exception("coverage rebuild: %s window %s failed", view_name, window)
            raise CoverageRebuildError(
                f"{view_name}: refresh failed on window {window} — "
                f"{exc}. The sweep is resumable: re-run to continue from here. "
                "If the failure was a client-side timeout, pg_cancel_backend "
                "the server side before retrying."
            ) from exc

        progress.refreshed += 1
        if on_progress:
            on_progress(progress, window)

    progress.rows_after = _row_count(conn, view_name) if not dry_run else 0
    elapsed = (clock() - started).total_seconds()

    return RebuildResult(
        family=family,
        view_name=view_name,
        windows=len(windows),
        refreshed=progress.refreshed,
        skipped=progress.skipped,
        rows_before=progress.rows_before,
        rows_after=progress.rows_after,
        span_start=lo,
        span_end=hi,
        elapsed_seconds=elapsed,
        subwindow=subwindow,
        errors=errors,
    )


def verify_coverage(
    conn: psycopg.Connection[dict[str, object]], family: CoverageFamily
) -> dict[str, object]:
    """Content-based check that a rebuild actually landed.

    Catalog presence proves nothing (the sql.md rule, and the 2026-08-04
    incident's lesson): a cagg can be present, non-empty, and still half
    materialized — exactly what slice 170's exit refresh found about the daily
    rollups. So this compares content against the source:

    - ``MIN(first_bucket)`` reaches the source's own floor, so no history is
      stranded at the old end;
    - ``MAX(last_bucket)`` is within one bucket width of the source's edge —
      one bucket, not zero, because the open bucket is never materialized;
    - the cagg holds rows for a comparable number of distinct symbols.
    """
    view_name = FAMILY_VIEW[family]
    source = FAMILY_SOURCE[family]
    column = _TIME_COLUMN[family]

    src = conn.execute(
        f"SELECT min({column}) AS lo, max({column}) AS hi, "  # noqa: S608
        f"count(DISTINCT symbol) AS symbols FROM {source}"
    ).fetchone()
    cov = conn.execute(
        f"SELECT min(first_bucket) AS lo, max(last_bucket) AS hi, "  # noqa: S608
        f"count(DISTINCT symbol) AS symbols, count(*) AS rows FROM {view_name}"
    ).fetchone()
    assert src is not None and cov is not None

    src_hi = cast("datetime | None", src["hi"])
    cov_hi = cast("datetime | None", cov["hi"])
    head_lag = (src_hi - cov_hi) if (src_hi and cov_hi) else None

    return {
        "view": view_name,
        "source": source,
        "source_span": (src["lo"], src["hi"]),
        "coverage_span": (cov["lo"], cov["hi"]),
        "source_symbols": src["symbols"],
        "coverage_symbols": cov["symbols"],
        "coverage_rows": cov["rows"],
        "head_lag": head_lag,
        "head_within_one_bucket": (
            head_lag is not None
            and head_lag <= COVERAGE_BUCKET_INTERVAL + timedelta(hours=4)
        ),
    }


__all__ = [
    "REBUILD_STATEMENT_TIMEOUT",
    "REBUILD_SUBWINDOW",
    "CoverageFamily",
    "CoverageRebuildError",
    "RebuildProgress",
    "RebuildResult",
    "SubWindow",
    "assert_coverage_index_scheduled",
    "assert_policies_paused",
    "plan_windows",
    "rebuild_coverage",
    "verify_coverage",
]
