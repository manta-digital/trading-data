"""In-place hypertable re-chunking driver (slice 166).

Rewrites a hypertable's small chunks into epoch-aligned windows of
``MINUTE_OHLCV_CHUNK_INTERVAL`` (7 days), one window per transaction:

    stage window rows into a temp table
      -> drop_chunks() for the window (removes chunks AND their slices)
      -> INSERT the rows back (tuple routing creates one fresh 7-day chunk)
      -> COMMIT; then compress the new chunk

This mechanism (Option D of the slice 166 design's Root-Cause Record) exists
because ``merge_chunks`` cannot merge chunks separated by empty ranges, which
is the normal state of market-hours data. The per-window transaction makes an
interrupted run leave a valid, partially-rewritten table — never a broken one
(rehearsed on a scratch hypertable, including mid-cycle rollback).

Window state is derived from the Timescale catalog on every run, so the
driver is idempotent and resumable: finished windows are skipped, a window
interrupted between COMMIT and compression is finished by compressing only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

import psycopg
from psycopg import sql as _sql
from psycopg.rows import dict_row

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_OHLCV_CHUNK_INTERVAL,
    Granularity,
)
from manta_trading.logging import get_logger

logger = get_logger(__name__)

RECHUNK_TABLE: str = "minute_ohlcv"
"""The hypertable this maintenance run targets (parameterized for tests only)."""

MINUTE_CAGG_GRANULARITIES: tuple[Granularity, ...] = (
    Granularity.M5,
    Granularity.M15,
    Granularity.H1,
    Granularity.H4,
)
"""The caggs whose refresh policies must be paused during a rechunk run."""


class WindowState(StrEnum):
    """Classification of one epoch-aligned target window."""

    DONE = "DONE"
    """Single chunk, slice equals the window, compressed."""

    COMPRESS_ONLY = "COMPRESS_ONLY"
    """Single aligned chunk left uncompressed (crash between commit and
    compress on a previous run) — just compress it."""

    REWRITE = "REWRITE"
    """Multiple chunks or misaligned slice, all compressed — full cycle."""

    SKIP_UNCOMPRESSED = "SKIP_UNCOMPRESSED"
    """Window still contains uncompressed chunks (the trailing region inside
    the compress_after horizon) — logged and left for a later re-run."""


@dataclass
class Window:
    start: datetime
    end: datetime
    chunks: list[str]
    state: WindowState


@dataclass
class RechunkResult:
    total_windows: int
    rewritten: int
    compressed_only: int
    skipped_uncompressed: int
    already_done: int
    dry_run: bool


class PreflightError(RuntimeError):
    """A pre-flight assertion failed; nothing was mutated."""


class RechunkError(RuntimeError):
    """A window cycle failed; the failing window is identified in the message."""


def _window_start(ts: datetime, interval: timedelta) -> datetime:
    """Epoch-aligned window start containing ``ts`` (TimescaleDB grid)."""
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    seconds = int((ts - epoch).total_seconds())
    step = int(interval.total_seconds())
    return epoch + timedelta(seconds=(seconds // step) * step)


def _load_windows(
    conn: psycopg.Connection, table: str, interval: timedelta
) -> list[Window]:
    """Group the hypertable's chunks into epoch-aligned windows and classify."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT format('%%I.%%I', chunk_schema, chunk_name) AS chunk, "
            "       range_start, range_end, is_compressed "
            "FROM timescaledb_information.chunks "
            "WHERE hypertable_name = %s ORDER BY range_start",
            (table,),
        )
        rows = cur.fetchall()

    grouped: dict[datetime, list[dict]] = {}
    for r in rows:
        grouped.setdefault(_window_start(r["range_start"], interval), []).append(r)

    windows: list[Window] = []
    for ws in sorted(grouped):
        chunk_rows = grouped[ws]
        we = ws + interval
        aligned = (
            len(chunk_rows) == 1
            and chunk_rows[0]["range_start"] == ws
            and chunk_rows[0]["range_end"] == we
        )
        all_compressed = all(r["is_compressed"] for r in chunk_rows)
        if aligned and all_compressed:
            state = WindowState.DONE
        elif aligned:
            state = WindowState.COMPRESS_ONLY
        elif all_compressed:
            state = WindowState.REWRITE
        else:
            state = WindowState.SKIP_UNCOMPRESSED
        windows.append(
            Window(ws, we, [r["chunk"] for r in chunk_rows], state)
        )
    return windows


def _assert_dimension_interval(
    conn: psycopg.Connection, table: str, interval: timedelta
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT time_interval FROM timescaledb_information.dimensions "
            "WHERE hypertable_name = %s",
            (table,),
        )
        row = cur.fetchone()
    if row is None:
        raise PreflightError(f"{table} is not a hypertable on this database")
    if row[0] != interval:
        raise PreflightError(
            f"{table} chunk_time_interval is {row[0]}, expected {interval} — "
            "apply migration 043 (mt data migrate apply) before rechunking"
        )


def _resolve_paused_job_violations(
    conn: psycopg.Connection, table: str, cagg_views: tuple[str, ...]
) -> list[str]:
    """Return descriptions of minute-family jobs that are still scheduled.

    Job IDs are resolved from the catalog at runtime — never hardcoded. The
    family is: the table's columnstore/compression policy plus the refresh
    policies of the given cagg views (``jobs.hypertable_name`` carries the
    *view* name for cagg refresh policies).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT job_id, proc_name, hypertable_name "
            "FROM timescaledb_information.jobs "
            "WHERE scheduled "
            "  AND ((proc_name = 'policy_compression' AND hypertable_name = %s) "
            "    OR (proc_name = 'policy_refresh_continuous_aggregate' "
            "        AND hypertable_name = ANY(%s)))",
            (table, list(cagg_views)),
        )
        return [
            f"job {job_id} ({proc} on {ht})" for job_id, proc, ht in cur.fetchall()
        ]


def _rewrite_window(
    conn: psycopg.Connection,
    table: str,
    window: Window,
    after_stage: Callable[[Window], None] | None = None,
) -> int:
    """Run one atomic stage -> drop_chunks -> reinsert cycle. Returns rows moved.

    The EXCLUSIVE table lock is taken BEFORE the stage snapshot: a concurrent
    application writer (daemon, ``mt data pull``, gap seeding) committing into
    the window between staging and drop_chunks would otherwise have its rows
    destroyed by the drop and never reinserted — silently, because the
    staged==reinserted guard cannot see rows it never staged. EXCLUSIVE blocks
    writers for the duration of one window (~seconds) while leaving readers
    unaffected. ``after_stage`` is a test seam (fires inside the transaction,
    between staging and drop) — never set it in production use.
    """
    tbl = _sql.Identifier(table)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(_sql.SQL("LOCK TABLE {} IN EXCLUSIVE MODE").format(tbl))
            cur.execute(
                _sql.SQL(
                    "CREATE TEMP TABLE _rechunk_stage ON COMMIT DROP AS "
                    "SELECT * FROM {} WHERE time >= %s AND time < %s"
                ).format(tbl),
                (window.start, window.end),
            )
            if after_stage is not None:
                after_stage(window)
            staged_row = cur.execute("SELECT count(*) FROM _rechunk_stage").fetchone()
            assert staged_row is not None  # count(*) always returns one row
            staged: int = staged_row[0]
            dropped_row = cur.execute(
                "SELECT count(*) FROM ("
                "  SELECT drop_chunks(%s, older_than => %s::timestamptz, "
                "                     newer_than => %s::timestamptz)"
                ") AS d",
                (table, window.end, window.start),
            ).fetchone()
            assert dropped_row is not None  # count(*) always returns one row
            dropped: int = dropped_row[0]
            if dropped != len(window.chunks):
                raise RechunkError(
                    f"window {window.start:%Y-%m-%d}: expected to drop "
                    f"{len(window.chunks)} chunks, dropped {dropped} — aborting"
                )
            cur.execute(
                _sql.SQL("INSERT INTO {} SELECT * FROM _rechunk_stage").format(tbl)
            )
            if cur.rowcount != staged:
                # Raising inside conn.transaction() rolls the whole cycle back.
                raise RechunkError(
                    f"window {window.start:%Y-%m-%d}: staged {staged} rows but "
                    f"reinserted {cur.rowcount} — rolled back"
                )
    return staged


def _compress_window(conn: psycopg.Connection, table: str, window: Window) -> None:
    """Compress the window's chunk(s) after a rewrite (or to finish one)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT format('%%I.%%I', chunk_schema, chunk_name) "
            "FROM timescaledb_information.chunks "
            "WHERE hypertable_name = %s AND range_start >= %s AND range_end <= %s",
            (table, window.start, window.end),
        )
        chunks = [r[0] for r in cur.fetchall()]
        if len(chunks) != 1:
            raise RechunkError(
                f"window {window.start:%Y-%m-%d}: expected exactly 1 chunk "
                f"after rewrite, found {len(chunks)} ({chunks}) — aborting"
            )
        cur.execute(
            "SELECT compress_chunk(%s::regclass, if_not_compressed => true)",
            (chunks[0],),
        )
    conn.commit()


def run_rechunk(
    conninfo: str,
    *,
    dry_run: bool = False,
    table: str = RECHUNK_TABLE,
    cagg_views: tuple[str, ...] | None = None,
    max_windows: int | None = None,
    after_stage: Callable[[Window], None] | None = None,
) -> RechunkResult:
    """Re-chunk ``table`` into MINUTE_OHLCV_CHUNK_INTERVAL windows.

    ``table``/``cagg_views``/``max_windows``/``after_stage`` are test seams,
    not operator configuration — the CLI exposes only ``--dry-run``.
    """
    interval = MINUTE_OHLCV_CHUNK_INTERVAL
    if cagg_views is None:
        cagg_views = tuple(GRANULARITY_SOURCE[g] for g in MINUTE_CAGG_GRANULARITIES)

    with psycopg.connect(conninfo) as conn:
        # Pre-flight guards mutation only; a dry run is read-only and must
        # work before migration 043 is applied and before jobs are paused,
        # so the operator can inspect the plan first.
        if not dry_run:
            _assert_dimension_interval(conn, table, interval)
            still_scheduled = _resolve_paused_job_violations(conn, table, cagg_views)
            if still_scheduled:
                raise PreflightError(
                    "refusing to run: background jobs still scheduled — "
                    + "; ".join(still_scheduled)
                )
        conn.commit()

        windows = _load_windows(conn, table, interval)
        conn.commit()
        todo = [
            w
            for w in windows
            if w.state in (WindowState.REWRITE, WindowState.COMPRESS_ONLY)
        ]
        skipped = [w for w in windows if w.state == WindowState.SKIP_UNCOMPRESSED]
        done = len(windows) - len(todo) - len(skipped)

        logger.info(
            "rechunk %s: %d windows total — %d to rewrite, %d to compress-only, "
            "%d skipped (uncompressed trailing), %d already done%s",
            table,
            len(windows),
            sum(1 for w in todo if w.state == WindowState.REWRITE),
            sum(1 for w in todo if w.state == WindowState.COMPRESS_ONLY),
            len(skipped),
            done,
            " [DRY RUN]" if dry_run else "",
        )
        for w in skipped:
            logger.info(
                "skipping window %s..%s: %d chunk(s) not yet compressed",
                f"{w.start:%Y-%m-%d}", f"{w.end:%Y-%m-%d}", len(w.chunks),
            )

        rewritten = 0
        compressed_only = 0
        if not dry_run:
            for i, w in enumerate(todo, start=1):
                if max_windows is not None and i > max_windows:
                    logger.info("stopping after %d windows (max_windows)", max_windows)
                    break
                if w.state == WindowState.REWRITE:
                    rows = _rewrite_window(conn, table, w, after_stage)
                    if rows > 0:
                        _compress_window(conn, table, w)
                    rewritten += 1
                    logger.info(
                        "rewrote window %d/%d (%s, %d chunks -> %d, %d rows)",
                        i, len(todo), f"{w.start:%Y-%m-%d}", len(w.chunks),
                        1 if rows > 0 else 0, rows,
                    )
                else:
                    _compress_window(conn, table, w)
                    compressed_only += 1
                    logger.info(
                        "compressed window %d/%d (%s)",
                        i, len(todo), f"{w.start:%Y-%m-%d}",
                    )

    return RechunkResult(
        total_windows=len(windows),
        rewritten=rewritten,
        compressed_only=compressed_only,
        skipped_uncompressed=len(skipped),
        already_done=done,
        dry_run=dry_run,
    )
