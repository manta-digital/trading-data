"""Windowed re-materialization sweep for the minute continuous aggregates
(slice 163).

Repairs the two defects the slice targets in one pass, one cagg at a time, over
70-day epoch-grid windows oldest → newest:

    parity check → (if PENDING) drop_chunks → refresh_continuous_aggregate(force)
        → compress_chunk

Dropping the window's old ~1.67-day chunks lets the forced refresh rebuild the
window from raw into a single fresh 70-day chunk (migration 044 set the
interval), which is then compressed behind the frontier (migration 045 enabled
columnstore). ``refresh_continuous_aggregate`` cannot run inside a transaction
block, so the three steps commit independently — there is no per-window
transaction. State is therefore **parity-derived, not bookkept**: a window is
DONE iff its cagg SUM(minute_count) equals the raw COUNT(*), so an interrupted
run resumes by re-deriving that on the next invocation (D1 crash-window
enumeration).

Pre-flight refuses (never warns) unless the target cagg's refresh policy and
columnstore policy are paused, migration 044 is applied, and the operator has
attested disk headroom. Raw-table jobs are never touched — this slice does not
restructure the raw hypertable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast

import psycopg

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_CAGG_CHUNK_INTERVAL,
    MINUTE_OHLCV_TABLE,
    Granularity,
)
from manta_trading.logging import get_logger
from manta_trading.market.maintenance.cagg_parity import (
    WindowParity,
    _epoch_grid_windows,
    _raw_bounds,
    _TimeoutConnection,
    cagg_view,
)
from manta_trading.market.maintenance.rechunk import PreflightError

logger = get_logger(__name__)

# Recommended operator order for the per-granularity repair runs. The 4h cagg
# goes FIRST because it is both a repair target and the daemon coverage-index
# source (pre-flight check 4): repair it while its own policies are paused,
# resume its refresh policy plus the catch-up refresh (runbook R2), and only
# then repair the remaining three with the 4h cagg back in service. A combined
# all-cagg sweep is refused up-front — no static pause configuration satisfies
# checks 1 and 4 simultaneously for every target.
REPAIR_RUN_ORDER: tuple[Granularity, ...] = (
    Granularity.H4,
    Granularity.H1,
    Granularity.M15,
    Granularity.M5,
)


class RepairError(RuntimeError):
    """A window cycle failed; the failing window is identified in the message."""


@dataclass
class RepairResult:
    """Outcome of a repair sweep over one or more caggs."""

    dry_run: bool
    per_cagg: dict[Granularity, CaggRepairOutcome] = field(default_factory=dict)


@dataclass
class CaggRepairOutcome:
    granularity: Granularity
    view_name: str
    total_windows: int
    already_done: int
    rebuilt: int
    planned_pending: int  # dry-run: windows that WOULD be rebuilt


# ---------------------------------------------------------------------------
# Pre-flight (Task C1)
# ---------------------------------------------------------------------------

# Job proc_names, defined once. The refresh policy re-materializes the trailing
# head; the columnstore policy compresses aged chunks. Both must be paused for a
# cagg under repair (a policy firing mid drop/refresh is the journal's
# silent-loss collision).
_PROC_REFRESH: str = "policy_refresh_continuous_aggregate"
_PROC_COLUMNSTORE: str = "policy_compression"


# The cagg the minute daemon's coverage index reads (slice 162,
# build_minute_coverage_index). Resolved from GRANULARITY_SOURCE rather than
# spelled out, so the two paths cannot drift apart.
_COVERAGE_INDEX_VIEW: str = GRANULARITY_SOURCE[Granularity.H4]


_REFRESH_SUBWINDOW: timedelta = timedelta(days=14)
"""Maximum span of a single ``refresh_continuous_aggregate`` call.

A 70-day epoch window is rebuilt in 14-day refresh slices, never one call.
TimescaleDB's materialization batching does not engage for these caggs (server
log: "no valid batches produced ... falling back to single batch processing"),
so a refresh materializes its whole range as ONE in-memory tuplestore whose
size scales with the window — outside ``work_mem``'s control. A single 70-day
call on ``minute_4hour_ohlcv`` grew past the ~119 GB commit limit and was
OOM-killed twice on prod (2026-08-11; journal 20260805 established the class).
14-day slices are the proven ceiling: the 2026-08-06 minute_5min rebuild ran
119 windows of them without a memory event. Parity remains per 70-day window —
slicing only bounds each call's memory, not the resume/skip granularity."""


@dataclass(frozen=True)
class CaggJob:
    job_id: int
    proc_name: str
    view_name: str
    scheduled: bool


def _resolve_cagg_jobs(
    conn: psycopg.Connection[dict[str, object]], view_name: str
) -> list[CaggJob]:
    """All refresh/columnstore policy jobs for one cagg, from the catalog.

    ``timescaledb_information.jobs.hypertable_name`` carries the *view* name for
    both a cagg's refresh policy and (post-045) its columnstore policy. Job IDs
    are resolved at runtime — never hardcoded (design §Baseline)."""
    rows = conn.execute(
        "SELECT job_id, proc_name, hypertable_name, scheduled "
        "FROM timescaledb_information.jobs "
        "WHERE hypertable_name = %s AND proc_name = ANY(%s)",
        (view_name, [_PROC_REFRESH, _PROC_COLUMNSTORE]),
    ).fetchall()
    return [
        CaggJob(
            job_id=int(cast("int", row["job_id"])),
            proc_name=str(row["proc_name"]),
            view_name=view_name,
            scheduled=bool(row["scheduled"]),
        )
        for row in rows
    ]


def _mat_chunk_interval(
    conn: psycopg.Connection[dict[str, object]], view_name: str
) -> timedelta | None:
    """The mat hypertable's chunk_time_interval, resolved by cagg view name."""
    mat_row = conn.execute(
        "SELECT materialization_hypertable_name AS mat_name "
        "FROM timescaledb_information.continuous_aggregates "
        "WHERE view_name = %s",
        (view_name,),
    ).fetchone()
    if mat_row is None:
        raise PreflightError(
            f"continuous aggregate {view_name!r} not found in catalog"
        )
    interval_row = conn.execute(
        "SELECT time_interval FROM timescaledb_information.dimensions "
        "WHERE hypertable_name = %s",
        (mat_row["mat_name"],),
    ).fetchone()
    if interval_row is None or interval_row["time_interval"] is None:
        return None
    return cast("timedelta", interval_row["time_interval"])


def _check_coverage_index_available(
    conn: psycopg.Connection[dict[str, object]], view_name: str
) -> None:
    """Refuse if repairing ``view_name`` would starve the daemon coverage index.

    The minute daemon's coverage index reads ``_COVERAGE_INDEX_VIEW``
    (``build_minute_coverage_index``, slice 162). If that cagg's refresh policy
    is paused, its leading edge freezes while raw keeps growing, so
    ``compute_missing_minute_sessions`` sees recent sessions as missing and
    re-seeds gap rows for them every cycle — a silent, perpetual re-pull that
    costs provider calls and never self-heals (prod incident 2026-07-25).

    Repairing the coverage cagg *itself* legitimately requires pausing it, so
    that case is allowed — the sweep is bounded and the operator follows the
    catch-up refresh in the pausing runbook. What this refuses is the
    cross-granularity case: pausing the coverage cagg to repair a *different*
    one, where the pause buys nothing and the loop runs for the whole sweep.
    """
    if view_name == _COVERAGE_INDEX_VIEW:
        return

    rows = [
        j
        for j in _resolve_cagg_jobs(conn, _COVERAGE_INDEX_VIEW)
        if j.proc_name == _PROC_REFRESH
    ]
    paused = [j for j in rows if not j.scheduled]
    if not paused:
        return

    ids = ", ".join(str(j.job_id) for j in paused)
    resume_cmds = " ".join(
        f"SELECT alter_job({j.job_id}, scheduled => true);" for j in paused
    )
    raise PreflightError(
        f"refusing to repair {view_name}: the refresh policy for "
        f"{_COVERAGE_INDEX_VIEW} (job(s) {ids}) is paused. That cagg feeds the "
        "minute daemon's coverage index — leaving it paused through this sweep "
        "makes the daemon re-seed and re-pull recent sessions every cycle. "
        f"Resume it first: {resume_cmds} Then, if it was paused for longer than "
        "its start_offset, run a catch-up "
        f"CALL refresh_continuous_aggregate('{_COVERAGE_INDEX_VIEW}', "
        "<pause_start - 1 day>, <now + 1 day>); "
        "see user/runbooks/300-cagg-maintenance-pausing.md"
    )


def preflight(
    conn: psycopg.Connection[dict[str, object]],
    view_name: str,
    *,
    assume_headroom_gb: float | None,
    required_headroom_gb: float,
) -> None:
    """Refuse (raise PreflightError) unless it is safe to repair ``view_name``.

    Checks, all refusing rather than warning:

    1. The cagg's refresh policy **and** columnstore policy (when present) are
       paused. The refusal message prints the exact job IDs and the pause
       command so the operator can act without guesswork.
    2. The mat hypertable's chunk_time_interval equals MINUTE_CAGG_CHUNK_INTERVAL
       (migration 044 applied) — otherwise the drop/refresh would rebuild into
       the wrong chunk shape.
    3. Disk headroom is attested. Standard PostgreSQL exposes no reliable
       free-disk-space source over a connection, so rather than silently
       skipping the check the operator must pass ``--assume-headroom-gb`` with a
       value at least ``required_headroom_gb``.
    4. The coverage-index cagg's refresh policy is still scheduled when a
       *different* cagg is the repair target — see
       ``_check_coverage_index_available``.

    Never touches raw-table jobs — assert-only guard 5 documents that the job
    query is scoped to the cagg view name, never ``minute_ohlcv``.
    """
    # Guard 5 (assertion): job resolution is scoped to the cagg view; the raw
    # table's own jobs are structurally out of reach of this pre-flight.
    assert view_name != MINUTE_OHLCV_TABLE, (
        "cagg repair pre-flight must never target the raw hypertable"
    )

    # Check 1: jobs paused.
    jobs = _resolve_cagg_jobs(conn, view_name)
    unpaused = [j for j in jobs if j.scheduled]
    if unpaused:
        ids = ", ".join(str(j.job_id) for j in unpaused)
        details = "; ".join(f"job {j.job_id} ({j.proc_name})" for j in unpaused)
        pause_cmds = " ".join(
            f"SELECT alter_job({j.job_id}, scheduled => false);" for j in unpaused
        )
        raise PreflightError(
            f"refusing to repair {view_name}: background job(s) still scheduled "
            f"— {details}. Pause them first: {pause_cmds} "
            f"(job ids: {ids})"
        )

    # Check 4: the daemon's coverage-index cagg must stay refreshed while a
    # different cagg is repaired (ordered after check 1 so the target's own
    # unpaused jobs — the more direct problem — are reported first).
    _check_coverage_index_available(conn, view_name)

    # Check 2: interval == constant (migration 044 applied).
    interval = _mat_chunk_interval(conn, view_name)
    if interval != MINUTE_CAGG_CHUNK_INTERVAL:
        raise PreflightError(
            f"refusing to repair {view_name}: mat hypertable chunk_time_interval "
            f"is {interval}, expected {MINUTE_CAGG_CHUNK_INTERVAL} — apply "
            "migration 044 (mt data migrate apply) before repairing"
        )

    # Check 3: disk headroom attested (no reliable in-SQL source — refuse).
    if assume_headroom_gb is None:
        raise PreflightError(
            f"refusing to repair {view_name}: disk headroom cannot be verified "
            "from SQL. Re-run with --assume-headroom-gb=<free GB on the DB "
            f"volume> (need at least {required_headroom_gb:.0f} GB)."
        )
    if assume_headroom_gb < required_headroom_gb:
        raise PreflightError(
            f"refusing to repair {view_name}: attested headroom "
            f"{assume_headroom_gb:.0f} GB < required {required_headroom_gb:.0f} GB"
        )


# ---------------------------------------------------------------------------
# Window sweep (Task C3)
# ---------------------------------------------------------------------------


def _window_parity(
    conn: psycopg.Connection[dict[str, object]],
    view_name: str,
    start: datetime,
    end: datetime,
) -> tuple[int, int]:
    """Return (raw_count, cagg_count) for one window (parity oracle)."""
    raw_row = conn.execute(
        f'SELECT COUNT(*) AS n FROM {MINUTE_OHLCV_TABLE} '  # noqa: S608 — module constant
        'WHERE "time" >= %s AND "time" < %s',
        (start, end),
    ).fetchone()
    assert raw_row is not None  # COUNT(*) always returns one row
    cagg_row = conn.execute(
        f"SELECT COALESCE(SUM(minute_count), 0) AS n FROM {view_name} "  # noqa: S608 — view from GRANULARITY_SOURCE
        "WHERE time_bucket >= %s AND time_bucket < %s",
        (start, end),
    ).fetchone()
    assert cagg_row is not None  # SUM(...) always returns one row
    return int(cast("int", raw_row["n"])), int(cast("int", cagg_row["n"]))


def _rebuild_window(
    conn: psycopg.Connection[dict[str, object]],
    view_name: str,
    start: datetime,
    end: datetime,
) -> None:
    """drop_chunks → refresh(force) → compress for one PENDING window.

    NOT wrapped in a transaction — refresh_continuous_aggregate cannot run in a
    transaction block, so the three statements commit independently. The
    connection must be in autocommit. A kill between steps leaves a state the
    next run's parity check detects and rebuilds (D1)."""
    # 1. Drop the window's existing (wrong-interval) cagg chunks and their
    #    dimension slices, so the refresh can create one fresh 70-day chunk.
    conn.execute(
        "SELECT drop_chunks(%s, older_than => %s::timestamptz, "
        "                   newer_than => %s::timestamptz)",
        (view_name, end, start),
    )
    # 2. Rebuild the window from raw. force => true because the corrupted
    #    region's invalidation entries were already consumed (a plain refresh
    #    would no-op — journal cagg-collision entry). Refreshed in
    #    _REFRESH_SUBWINDOW slices: one 70-day call materializes as a single
    #    batch and exceeds the host's memory (see the constant's docstring).
    slice_start = start
    while slice_start < end:
        slice_end = min(slice_start + _REFRESH_SUBWINDOW, end)
        conn.execute(
            "CALL refresh_continuous_aggregate(%s, %s::timestamptz, "
            "%s::timestamptz, force => true)",
            (view_name, slice_start, slice_end),
        )
        slice_start = slice_end
    # 3. Compress the window's freshly-created chunk(s) behind the frontier. A
    #    grid-straddling table edge can yield two chunks — compress all
    #    uncompressed chunks in the window.
    _compress_window(conn, view_name, start, end)


def _compress_window(
    conn: psycopg.Connection[dict[str, object]],
    view_name: str,
    start: datetime,
    end: datetime,
) -> None:
    """Compress every uncompressed chunk of the cagg within the window.

    Resolves the mat hypertable's chunks by view name via the catalog and
    compresses those overlapping [start, end)."""
    mat_row = conn.execute(
        "SELECT materialization_hypertable_name AS mat_name "
        "FROM timescaledb_information.continuous_aggregates WHERE view_name = %s",
        (view_name,),
    ).fetchone()
    assert mat_row is not None  # caller already validated the cagg exists
    mat_name = mat_row["mat_name"]
    chunks = conn.execute(
        "SELECT format('%%I.%%I', chunk_schema, chunk_name) AS chunk "
        "FROM timescaledb_information.chunks "
        "WHERE hypertable_name = %s AND range_start < %s AND range_end > %s "
        "  AND NOT is_compressed",
        (mat_name, end, start),
    ).fetchall()
    for row in chunks:
        conn.execute(
            "SELECT compress_chunk(%s::regclass, if_not_compressed => true)",
            (row["chunk"],),
        )


def _repair_one_cagg(
    conn: psycopg.Connection[dict[str, object]],
    granularity: Granularity,
    windows: list[tuple[datetime, datetime]],
    *,
    dry_run: bool,
    progress: Callable[[str], None],
) -> CaggRepairOutcome:
    """Sweep one cagg over the given oldest→newest windows."""
    view_name = cagg_view(granularity)
    already_done = 0
    rebuilt = 0
    planned_pending = 0

    for i, (start, end) in enumerate(windows, start=1):
        raw_count, cagg_count = _window_parity(conn, view_name, start, end)
        parity = (
            WindowParity.DONE if raw_count == cagg_count else WindowParity.PENDING
        )
        if parity is WindowParity.DONE:
            already_done += 1
            continue
        if dry_run:
            planned_pending += 1
            progress(
                f"[dry-run] {view_name} window {i}/{len(windows)} "
                f"{start:%Y-%m-%d}..{end:%Y-%m-%d} PENDING "
                f"(raw {raw_count:,} vs cagg {cagg_count:,})"
            )
            continue
        started = _now(conn)
        try:
            _rebuild_window(conn, view_name, start, end)
        except psycopg.OperationalError:
            # statement_timeout / lost connection: propagate unchanged so
            # _TimeoutConnection.__exit__ still sees the type it cancels the
            # backend for, and the CLI reports it as a database error.
            raise
        except psycopg.Error as exc:
            # Any other DB failure (ProgrammingError, InternalError from
            # drop_chunks/refresh/compress): identify the failing window per
            # the RepairError contract; the CLI maps this to exit code 2. The
            # window stays PENDING by parity, so a re-run resumes here.
            raise RepairError(
                f"{view_name} window {i}/{len(windows)} "
                f"{start:%Y-%m-%d}..{end:%Y-%m-%d} rebuild failed: {exc}"
            ) from exc
        rebuilt += 1
        progress(
            f"{view_name} window {i}/{len(windows)} "
            f"{start:%Y-%m-%d}..{end:%Y-%m-%d} rebuilt "
            f"(raw {raw_count:,}, {_elapsed(conn, started)})"
        )

    return CaggRepairOutcome(
        granularity=granularity,
        view_name=view_name,
        total_windows=len(windows),
        already_done=already_done,
        rebuilt=rebuilt,
        planned_pending=planned_pending,
    )


def _now(conn: psycopg.Connection[dict[str, object]]) -> datetime:
    """Server clock (avoids the sandboxed client's unavailable wall clock)."""
    row = conn.execute("SELECT clock_timestamp() AS ts").fetchone()
    assert row is not None
    return cast("datetime", row["ts"])


def _elapsed(conn: psycopg.Connection[dict[str, object]], since: datetime) -> str:
    delta = _now(conn) - since
    return f"{delta.total_seconds():.1f}s"


# Estimated per-cagg peak uncompressed footprint (one 70-day window) used by the
# headroom pre-flight. Order-of the design's full-uncompressed estimates ÷ ~117
# windows, rounded up and floored at a safety margin. Single source here.
_REQUIRED_HEADROOM_GB: float = 20.0


def run_repair(
    conninfo: str,
    granularities: tuple[Granularity, ...],
    *,
    dry_run: bool = False,
    assume_headroom_gb: float | None = None,
    progress: Callable[[str], None] | None = None,
    interval: timedelta = MINUTE_CAGG_CHUNK_INTERVAL,
) -> RepairResult:
    """Repair the given minute caggs by re-materializing PENDING windows.

    Sweeps one cagg at a time in the order given. ``granularities`` has no
    default: a real (non-dry) repair run targets exactly ONE cagg per
    invocation — pre-flight checks 1 and 4 cannot both hold across an all-cagg
    sweep (the 4h cagg is both a repair target and the coverage-index source),
    so the CLI refuses multi-cagg real runs and the recommended per-run
    sequence lives in ``REPAIR_RUN_ORDER``. Multi-cagg tuples are valid for
    ``dry_run`` (read-only, pre-flight skipped so the plan is inspectable
    before jobs are paused).

    For each cagg: pre-flight (skipped for dry-run), then window-by-window
    parity-check → rebuild. Resumable and Ctrl-C safe by parity, not
    transactionality (see module docstring). Returns per-cagg outcomes.

    ``assume_headroom_gb``/``interval`` are operator/test inputs; ``progress``
    receives one line per window (defaults to the module logger).
    """
    emit = progress if progress is not None else logger.info
    result = RepairResult(dry_run=dry_run)

    # Windows are enumerated once against the raw bounds (shared across caggs).
    # A read-only timeout connection derives bounds; the sweep itself needs
    # autocommit (refresh_continuous_aggregate forbids a txn block), opened
    # per cagg below.
    with _TimeoutConnection(conninfo) as ro_conn:
        bounds = _raw_bounds(ro_conn)
    if bounds is None:
        logger.warning("raw table %s is empty — nothing to repair", MINUTE_OHLCV_TABLE)
        for gran in granularities:
            result.per_cagg[gran] = CaggRepairOutcome(
                granularity=gran,
                view_name=cagg_view(gran),
                total_windows=0,
                already_done=0,
                rebuilt=0,
                planned_pending=0,
            )
        return result
    windows = _epoch_grid_windows(bounds[0], bounds[1], interval)

    for gran in granularities:
        view_name = cagg_view(gran)
        # Autocommit connection for the mutating sweep — refresh_continuous_
        # aggregate forbids a txn block, so each statement commits on its own.
        # _TimeoutConnection sets the statement_timeout and cancels the
        # server-side backend if the operator Ctrl-C's mid-window (design F005);
        # the next run resumes that window via its parity check.
        with _TimeoutConnection(conninfo, autocommit=True) as conn:
            if not dry_run:
                preflight(
                    conn,
                    view_name,
                    assume_headroom_gb=assume_headroom_gb,
                    required_headroom_gb=_REQUIRED_HEADROOM_GB,
                )
            outcome = _repair_one_cagg(
                conn, gran, windows, dry_run=dry_run, progress=emit
            )
        result.per_cagg[gran] = outcome
        emit(
            f"{view_name}: {outcome.total_windows} windows — "
            f"{outcome.already_done} already at parity, "
            + (
                f"{outcome.planned_pending} would rebuild [DRY RUN]"
                if dry_run
                else f"{outcome.rebuilt} rebuilt"
            )
        )
    return result
