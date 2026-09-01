"""Cagg-vs-source parity computation for the minute continuous aggregates
(slice 163).

The four minute caggs are ``materialized_only = true``: a materialized region
holding fewer rows than the raw table serves that deficit *as truth*. The only
way to detect this self-hiding corruption is a direct comparison of the cagg's
``SUM(minute_count)`` against the raw ``COUNT(*)`` over the same time window
(journal 20260720: "only a direct cagg-vs-source comparison can detect it").

This module is the read-only detector behind ``mt data caggs verify`` and the
resumability oracle behind ``mt data caggs repair``: a 70-day epoch-grid window
is DONE iff its cagg count equals its raw count.

Discipline (journal 20260720 / review F005): every prod query runs under an
explicit ``statement_timeout``; on client interrupt or timeout the server-side
backend is cancelled before the exception propagates, so a killed CLI never
leaves a runaway scan on prod. This module has **zero** mutation paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import TracebackType
from typing import Literal, cast

import psycopg
from psycopg.rows import dict_row

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_CAGG_CHUNK_INTERVAL,
    MINUTE_CAGG_GRANULARITIES,
    MINUTE_CAGG_MAINTENANCE_STATEMENT_TIMEOUT,
    MINUTE_OHLCV_TABLE,
    Granularity,
)
from manta_trading.logging import get_logger
from manta_trading.market.maintenance.rechunk import _window_start

logger = get_logger(__name__)


def cagg_view(granularity: Granularity) -> str:
    """Continuous-aggregate view name for a minute granularity."""
    return GRANULARITY_SOURCE[granularity]


class WindowParity(StrEnum):
    """Parity state of one 70-day epoch-grid window for one cagg."""

    DONE = "DONE"
    """Cagg SUM(minute_count) == raw COUNT(*) over the window — fully
    materialized, nothing to repair."""

    PENDING = "PENDING"
    """Cagg count differs from raw count (under-materialized, empty, or — never
    observed but detected all the same — over-materialized) — repair rebuilds
    this window."""


@dataclass(frozen=True)
class WindowCounts:
    """Raw-vs-cagg row counts for one window and the derived parity state."""

    start: datetime
    end: datetime
    raw_count: int
    cagg_count: int

    @property
    def parity(self) -> WindowParity:
        return (
            WindowParity.DONE
            if self.raw_count == self.cagg_count
            else WindowParity.PENDING
        )

    @property
    def coverage(self) -> float:
        """Cagg count as a fraction of raw count (0.0 when raw is empty)."""
        if self.raw_count == 0:
            return 0.0
        return self.cagg_count / self.raw_count


@dataclass(frozen=True)
class YearParity:
    """Per-year rollup of window counts (verify's default report granularity)."""

    year: int
    raw_count: int
    cagg_count: int

    @property
    def parity(self) -> WindowParity:
        return (
            WindowParity.DONE
            if self.raw_count == self.cagg_count
            else WindowParity.PENDING
        )

    @property
    def coverage(self) -> float:
        if self.raw_count == 0:
            return 0.0
        return self.cagg_count / self.raw_count


@dataclass(frozen=True)
class CaggChunkSummary:
    """Catalog summary of one cagg's materialized hypertable shape."""

    view_name: str
    chunk_count: int
    chunk_interval: timedelta | None


@dataclass(frozen=True)
class CaggParityReport:
    """Full parity result for one cagg: per-window, per-year, chunk shape."""

    granularity: Granularity
    view_name: str
    windows: list[WindowCounts]
    years: list[YearParity]
    chunk_summary: CaggChunkSummary

    @property
    def raw_total(self) -> int:
        return sum(w.raw_count for w in self.windows)

    @property
    def cagg_total(self) -> int:
        return sum(w.cagg_count for w in self.windows)

    @property
    def in_parity(self) -> bool:
        """True iff every window is DONE (whole-cagg parity)."""
        return all(w.parity is WindowParity.DONE for w in self.windows)


def _epoch_grid_windows(
    raw_min: datetime, raw_max: datetime, interval: timedelta
) -> list[tuple[datetime, datetime]]:
    """Enumerate epoch-grid windows covering ``[raw_min, raw_max]``.

    Windows are aligned to the TimescaleDB grid (1970-01-01 + k×interval) so a
    window's bounds match the chunk boundaries the repair sweep creates. A range
    that straddles a grid line yields two windows (adjacency, journal)."""
    windows: list[tuple[datetime, datetime]] = []
    start = _window_start(raw_min, interval)
    while start <= raw_max:
        windows.append((start, start + interval))
        start = start + interval
    return windows


def _cancel_backend(conninfo: str, pid: int) -> None:
    """Cancel a server-side backend from a fresh connection (interrupt path).

    Best-effort: a failure to cancel must not mask the original interrupt, so
    the caller wraps this and always re-raises the triggering exception."""
    try:
        with psycopg.connect(conninfo, autocommit=True) as cancel_conn:
            cancel_conn.execute("SELECT pg_cancel_backend(%s)", (pid,))
        logger.warning("cancelled server-side backend pid=%s after interrupt", pid)
    except psycopg.Error:
        # The backend may have already finished or the DB may be unreachable;
        # either way the original interrupt is what matters. Log and move on.
        logger.exception("failed to cancel backend pid=%s", pid)


class _TimeoutConnection:
    """Connection wrapper: statement_timeout set, backend cancelled on
    interrupt/timeout before the exception propagates.

    Used read-only by parity (``autocommit=False``, the default) and by the
    repair sweep with ``autocommit=True`` — ``refresh_continuous_aggregate``
    cannot run in a transaction block, so the sweep needs each statement to
    commit on its own, while still getting the same Ctrl-C backend-cancel
    discipline (design F005).

    Usage::

        with _TimeoutConnection(conninfo) as conn:
            conn.execute(...)   # runs under MINUTE_CAGG_MAINTENANCE_STATEMENT_TIMEOUT
    """

    def __init__(self, conninfo: str, *, autocommit: bool = False) -> None:
        self._conninfo = conninfo
        self._autocommit = autocommit
        self._conn: psycopg.Connection[dict[str, object]] | None = None
        self._pid: int | None = None

    def __enter__(self) -> psycopg.Connection[dict[str, object]]:
        conn = psycopg.connect(
            self._conninfo, row_factory=dict_row, autocommit=self._autocommit
        )
        try:
            conn.execute(
                f"SET statement_timeout = "
                f"'{MINUTE_CAGG_MAINTENANCE_STATEMENT_TIMEOUT}'"
            )
            row = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()
            assert row is not None  # pg_backend_pid always returns one row
        except BaseException:
            # __exit__ never runs if setup fails before the with-body is
            # entered — close deterministically instead of leaking to the GC,
            # then re-raise unchanged.
            conn.close()
            raise
        self._pid = int(cast("int", row["pid"]))
        self._conn = conn
        return conn

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        # On KeyboardInterrupt or an OperationalError (statement_timeout arrives
        # as one), cancel the still-running backend on a separate connection
        # before the primary connection is torn down. Never suppress: return
        # False so the exception propagates.
        if (
            exc_type is not None
            and issubclass(exc_type, (KeyboardInterrupt, psycopg.OperationalError))
            and self._pid is not None
        ):
            _cancel_backend(self._conninfo, self._pid)
        if self._conn is not None:
            self._conn.close()
        return False


def _raw_bounds(
    conn: psycopg.Connection[dict[str, object]],
) -> tuple[datetime, datetime] | None:
    """Return ``(min(time), max(time))`` of the raw table, or None if empty.

    Uses the chunk catalog for the coarse bound then a bounded MIN/MAX so the
    planner prunes chunks (an un-bounded MIN/MAX over a 4.4-billion-row
    hypertable scans every chunk — journal 20260720)."""
    row = conn.execute(
        "SELECT MIN(range_start) AS lo, MAX(range_end) AS hi "
        "FROM timescaledb_information.chunks WHERE hypertable_name = %s",
        (MINUTE_OHLCV_TABLE,),
    ).fetchone()
    if row is None or row["lo"] is None or row["hi"] is None:
        return None
    lo_chunk, hi_chunk = row["lo"], row["hi"]
    bounds = conn.execute(
        f'SELECT MIN("time") AS lo, MAX("time") AS hi FROM {MINUTE_OHLCV_TABLE} '  # noqa: S608 — table is a shared constant
        'WHERE "time" >= %s AND "time" < %s',
        (lo_chunk, hi_chunk),
    ).fetchone()
    if bounds is None or bounds["lo"] is None or bounds["hi"] is None:
        return None
    return cast("datetime", bounds["lo"]), cast("datetime", bounds["hi"])


def _raw_window_counts(
    conn: psycopg.Connection[dict[str, object]],
    windows: list[tuple[datetime, datetime]],
) -> list[int]:
    """Raw COUNT(*) per window — cagg-independent, so computed exactly once
    per verify run and shared across all requested caggs (review F003: the raw
    side is the expensive scan; re-running it per cagg quadruples the heaviest
    prod queries for no new information)."""
    counts: list[int] = []
    for start, end in windows:
        raw_row = conn.execute(
            f'SELECT COUNT(*) AS n FROM {MINUTE_OHLCV_TABLE} '  # noqa: S608 — table is a shared constant
            'WHERE "time" >= %s AND "time" < %s',
            (start, end),
        ).fetchone()
        assert raw_row is not None  # COUNT(*) always returns one row
        counts.append(int(cast("int", raw_row["n"])))
    return counts


def _cagg_window_counts(
    conn: psycopg.Connection[dict[str, object]],
    view_name: str,
    windows: list[tuple[datetime, datetime]],
) -> list[int]:
    """Cagg SUM(minute_count) per window for one cagg view."""
    counts: list[int] = []
    for start, end in windows:
        cagg_row = conn.execute(
            f"SELECT COALESCE(SUM(minute_count), 0) AS n FROM {view_name} "  # noqa: S608 — view resolved from GRANULARITY_SOURCE
            "WHERE time_bucket >= %s AND time_bucket < %s",
            (start, end),
        ).fetchone()
        assert cagg_row is not None  # SUM(...) always returns one row
        counts.append(int(cast("int", cagg_row["n"])))
    return counts


def rollup_by_year(windows: list[WindowCounts]) -> list[YearParity]:
    """Aggregate window counts into per-year parity rows.

    A window is attributed to the calendar year of its start bound. 70-day
    windows do not align to year boundaries, so this is an approximate bucketing
    for the human-facing report — the authoritative parity signal is per-window
    (used by repair). Ascending year order."""
    by_year: dict[int, list[int]] = {}
    for w in windows:
        acc = by_year.setdefault(w.start.year, [0, 0])
        acc[0] += w.raw_count
        acc[1] += w.cagg_count
    return [
        YearParity(year=year, raw_count=raw, cagg_count=cagg)
        for year, (raw, cagg) in sorted(by_year.items())
    ]


def _chunk_summary(
    conn: psycopg.Connection[dict[str, object]], view_name: str
) -> CaggChunkSummary:
    """Chunk count and chunk_time_interval for a cagg's mat hypertable.

    Resolved by continuous-aggregate view name via the catalog (never by
    ``mat_N`` literal)."""
    mat_row = conn.execute(
        "SELECT format('%%I.%%I', materialization_hypertable_schema, "
        "              materialization_hypertable_name) AS mat, "
        "       materialization_hypertable_name AS mat_name "
        "FROM timescaledb_information.continuous_aggregates "
        "WHERE view_name = %s",
        (view_name,),
    ).fetchone()
    if mat_row is None:
        raise ValueError(f"continuous aggregate {view_name!r} not found in catalog")
    mat_name = mat_row["mat_name"]

    count_row = conn.execute(
        "SELECT COUNT(*) AS n FROM timescaledb_information.chunks "
        "WHERE hypertable_name = %s",
        (mat_name,),
    ).fetchone()
    assert count_row is not None  # COUNT(*) always returns one row

    interval_row = conn.execute(
        "SELECT time_interval FROM timescaledb_information.dimensions "
        "WHERE hypertable_name = %s",
        (mat_name,),
    ).fetchone()
    interval = (
        cast("timedelta", interval_row["time_interval"])
        if interval_row is not None and interval_row["time_interval"] is not None
        else None
    )
    return CaggChunkSummary(
        view_name=view_name,
        chunk_count=int(cast("int", count_row["n"])),
        chunk_interval=interval,
    )


def compute_parity(
    conninfo: str,
    granularities: tuple[Granularity, ...] = MINUTE_CAGG_GRANULARITIES,
    *,
    interval: timedelta = MINUTE_CAGG_CHUNK_INTERVAL,
) -> list[CaggParityReport]:
    """Compute cagg-vs-raw parity for the given minute caggs (read-only).

    Enumerates the 70-day epoch grid once over the raw table's bounds and
    computes the raw per-window counts **once** — the raw side is
    cagg-independent, so it is shared across every requested cagg rather than
    re-scanned per cagg. Then for each cagg computes per-window and per-year
    counts plus a chunk-shape summary. Every query runs under the maintenance
    statement_timeout with backend-cancel-on-interrupt (``_TimeoutConnection``).

    Returns one report per granularity, in the order given. An empty raw table
    yields reports with empty window/year lists (chunk summary still populated).
    """
    with _TimeoutConnection(conninfo) as conn:
        bounds = _raw_bounds(conn)
        windows = (
            _epoch_grid_windows(bounds[0], bounds[1], interval)
            if bounds is not None
            else []
        )
        raw_counts = _raw_window_counts(conn, windows)
        reports: list[CaggParityReport] = []
        for gran in granularities:
            view_name = cagg_view(gran)
            cagg_counts = _cagg_window_counts(conn, view_name, windows)
            window_counts = [
                WindowCounts(start=start, end=end, raw_count=raw, cagg_count=cagg)
                for (start, end), raw, cagg in zip(
                    windows, raw_counts, cagg_counts, strict=True
                )
            ]
            reports.append(
                CaggParityReport(
                    granularity=gran,
                    view_name=view_name,
                    windows=window_counts,
                    years=rollup_by_year(window_counts),
                    chunk_summary=_chunk_summary(conn, view_name),
                )
            )
    return reports
