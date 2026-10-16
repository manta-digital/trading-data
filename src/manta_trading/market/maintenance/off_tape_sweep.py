"""Mark all ``minute_ohlcv`` history with off-tape masks (slice 171).

One chunk per transaction, oldest first, single process (design "Sweep"):

    SET LOCAL lock_timeout -> LOCK the chunk (inserts wait, reads continue)
      -> raw checksum -> read the chunk's bars widened to whole ET days
      -> masks per symbol-day -> decompress_chunk (if compressed)
      -> UPDATE every row of the chunk -> raw checksum -> compress_chunk
      -> COMMIT

The lock comes first so no insert lands in the chunk between the read and the
write. An insert that waits re-marks its days after this commit, so the two
paths cannot disagree.

Progress is data state: a chunk is pending while it holds any ``NULL`` mark,
so a rerun skips finished chunks and redoes an interrupted one. The newest
chunk is deferred while a minute pass is running, so acquisition never waits
on a chunk rewrite. Failure handling follows the design's table: a lock
timeout skips the chunk and retries it once at the end; a checksum mismatch
rolls back and stops; anything else is logged and re-raised.
"""

from __future__ import annotations

import time as clock
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import psycopg

from manta_trading.constants import (
    MINUTE_OHLCV_TABLE,
    OFF_TAPE_DAY_TZ,
    OFF_TAPE_SWEEP_LOCK_TIMEOUT,
)
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunRepository
from manta_trading.data.quality.off_tape import mark_symbol_days
from manta_trading.data.quality.off_tape_store import (
    et_day_start,
    iter_symbol_frames,
    write_masks,
)
from manta_trading.logging import get_logger

logger = get_logger(__name__)

_CHUNKS = """
SELECT format('%%I.%%I', chunk_schema, chunk_name), range_start, range_end,
       is_compressed
FROM timescaledb_information.chunks
WHERE hypertable_name = %s
ORDER BY range_start
"""
_HAS_NULL = (
    "SELECT EXISTS (SELECT 1 FROM minute_ohlcv"
    " WHERE time >= %s AND time < %s AND off_tape IS NULL)"
)
#: Order-independent and constant-memory: no string_agg over millions of rows.
_RAW_CHECKSUM = """
SELECT count(*), sum(hashtextextended(
    concat_ws(',', symbol, time, open, high, low, close, volume), 0))
FROM minute_ohlcv WHERE time >= %s AND time < %s
"""


class ChecksumMismatch(RuntimeError):
    """Raw columns changed during a chunk rewrite; the chunk was rolled back."""


@dataclass(frozen=True)
class Chunk:
    """One ``minute_ohlcv`` chunk: qualified name, time range, compression."""

    name: str
    start: datetime
    end: datetime
    compressed: bool


@dataclass(frozen=True)
class ChunkResult:
    rows: int
    marked: int
    seconds: float


@dataclass
class SweepPlan:
    pending: list[Chunk]
    deferred: Chunk | None


@dataclass
class SweepReport:
    done: list[str] = field(default_factory=list)
    unlocked: list[str] = field(default_factory=list)
    deferred: str | None = None


def list_chunks(conn: psycopg.Connection[Any]) -> list[Chunk]:
    rows = conn.execute(_CHUNKS, (MINUTE_OHLCV_TABLE,)).fetchall()
    return [
        Chunk(name, start, end, bool(compressed))
        for name, start, end, compressed in rows
    ]


def _has_null(conn: psycopg.Connection[Any], chunk: Chunk) -> bool:
    row = conn.execute(_HAS_NULL, (chunk.start, chunk.end)).fetchone()
    return bool(row and row[0])


def minute_pass_running(conn: psycopg.Connection[Any]) -> bool:
    """True when ``pass_runs`` holds an open minute pass."""
    return bool(PassRunRepository(lambda: nullcontext(conn)).open_runs(PassKind.MINUTE))


def plan(conn: psycopg.Connection[Any]) -> SweepPlan:
    """Pending chunks oldest first; the newest is held back during a pass."""
    chunks = list_chunks(conn)
    pending = [chunk for chunk in chunks if _has_null(conn, chunk)]
    deferred = None
    if chunks and pending and pending[-1] == chunks[-1] and minute_pass_running(conn):
        deferred = pending.pop()
    return SweepPlan(pending, deferred)


def _checksum(conn: psycopg.Connection[Any], chunk: Chunk) -> tuple[object, ...]:
    row = conn.execute(_RAW_CHECKSUM, (chunk.start, chunk.end)).fetchone()
    assert row is not None  # an aggregate without GROUP BY always returns a row
    return tuple(row)


def _widened(chunk: Chunk) -> tuple[datetime, datetime]:
    """The chunk's range widened to whole ET days at both edges."""
    first = chunk.start.astimezone(OFF_TAPE_DAY_TZ).date()
    last = (chunk.end - timedelta(microseconds=1)).astimezone(OFF_TAPE_DAY_TZ).date()
    return et_day_start(first), et_day_start(last + timedelta(days=1))


def chunk_masks(conn: psycopg.Connection[Any], chunk: Chunk) -> pd.DataFrame:
    """Masks (``symbol, time, mask``) for every row inside ``chunk``."""
    start, end = _widened(chunk)
    parts: list[pd.DataFrame] = []
    for bars in iter_symbol_frames(conn, start, end):
        bars["mask"] = mark_symbol_days(bars)
        inside = (bars["time"] >= chunk.start) & (bars["time"] < chunk.end)
        parts.append(bars.loc[inside, ["symbol", "time", "mask"]])
    if not parts:
        return pd.DataFrame(columns=["symbol", "time", "mask"])
    return pd.concat(parts, ignore_index=True)


def sweep_chunk(conn: psycopg.Connection[Any], chunk: Chunk) -> ChunkResult:
    """Rewrite one chunk's masks in one transaction (module docstring)."""
    began = clock.perf_counter()
    with conn.transaction():
        conn.execute(f"SET LOCAL lock_timeout = '{OFF_TAPE_SWEEP_LOCK_TIMEOUT}'")
        conn.execute(f"LOCK TABLE {chunk.name} IN EXCLUSIVE MODE")
        before = _checksum(conn, chunk)
        masks = chunk_masks(conn, chunk)
        if chunk.compressed:
            conn.execute("SELECT decompress_chunk(%s::regclass)", (chunk.name,))
        rows = write_masks(conn, masks)
        if _checksum(conn, chunk) != before:
            raise ChecksumMismatch(f"raw checksum changed in {chunk.name}")
        if chunk.compressed:
            conn.execute("SELECT compress_chunk(%s::regclass)", (chunk.name,))
    marked = int((masks["mask"] != 0).sum()) if rows else 0
    return ChunkResult(rows, marked, clock.perf_counter() - began)


def _attempt(conn: psycopg.Connection[Any], chunk: Chunk, report: SweepReport) -> bool:
    """Sweep one chunk; False when its lock was not granted in time."""
    try:
        result = sweep_chunk(conn, chunk)
    except psycopg.errors.LockNotAvailable:
        # Design failure table: skip the locked chunk, retry it at the end.
        logger.warning("off-tape sweep: %s locked, skipped", chunk.name)
        return False
    except Exception:
        # ChecksumMismatch included: the chunk rolled back and the run stops.
        logger.exception("off-tape sweep: %s failed, rolled back", chunk.name)
        raise
    share = result.marked / result.rows if result.rows else 0.0
    logger.info(
        "off-tape sweep: %s rows=%d marked=%.3f%% elapsed=%.1fs",
        chunk.name,
        result.rows,
        share * 100,
        result.seconds,
    )
    report.done.append(chunk.name)
    return True


def run_sweep(conn: psycopg.Connection[Any]) -> SweepReport:
    """Sweep every pending chunk; lock-skipped chunks get one retry.

    ``conn`` must be in autocommit mode, so each chunk's ``transaction()``
    is a real transaction and its ``SET LOCAL`` ends with it.
    """
    if not conn.autocommit:
        raise ValueError("run_sweep needs an autocommit connection")
    swept = plan(conn)
    report = SweepReport(deferred=swept.deferred.name if swept.deferred else None)
    locked: list[Chunk] = []
    for chunk in swept.pending:
        if not _attempt(conn, chunk, report):
            locked.append(chunk)
    for chunk in locked:  # one retry each, after everything else
        if not _attempt(conn, chunk, report):
            report.unlocked.append(chunk.name)
    return report
