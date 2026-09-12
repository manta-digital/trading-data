"""Pass-run records: what is running, what ran, and how it ended.

Every all-active-scope pass (minute, daily, kalshi, health, accounting)
writes one row to the ``pass_runs`` table: an open row while it runs, closed
with an outcome when it finishes. The operator overview reads these rows;
the ``data_status`` view reads their walk anchors to decide staleness.

Design 922, Decision 1: rows are written by the process that runs the pass,
never by an observer.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PassKind(StrEnum):
    """Which recurring pass a ``pass_runs`` row belongs to.

    SQL cross-reference: pass_runs.pass column. The CHECK constraint is
    rendered from this enum by ``_pass_kind_check_sql()`` in
    migrations/minute.py. All code that reads or writes a kind MUST
    reference this enum — no bare string literals.
    """

    MINUTE = "minute"
    """The minute acquisition cycle: trailing phase plus backfill."""

    DAILY = "daily"
    """The daily acquisition cycle."""

    KALSHI = "kalshi"
    """The Kalshi collection pass."""

    HEALTH = "health"
    """The data-health check."""

    ACCOUNTING = "accounting"
    """The minute-accounting pass that computes the universe line."""


class PassRunOutcome(StrEnum):
    """How a pass ended (Design 922, Decision 2).

    SQL cross-reference: pass_runs.outcome column, NULL while the run is
    open. The CHECK constraint is rendered from this enum by
    ``_pass_run_outcome_check_sql()`` in migrations/minute.py.
    """

    COMPLETE = "COMPLETE"
    """The pass did all the work it set out to do."""

    COMPLETE_QUOTA = "COMPLETE_QUOTA"
    """The pass ran to the provider's daily quota. A result, not a failure."""

    INCOMPLETE = "INCOMPLETE"
    """The pass stopped before covering its work, without a provider fault."""

    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    """The provider could not be reached or refused to serve the pass."""

    FAILED = "FAILED"
    """The pass raised, or was abandoned by a process that no longer exists."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class PassRun:
    """One row from the pass_runs table.

    Fields mirror table columns exactly. Nullable columns use ``... | None``.

    ``walk_anchor_at`` is set only when the run attempted every active
    symbol: it is the instant from which "attempted in this walk" is
    measured, and the ``data_status`` view reads the newest such anchor per
    pass to decide STALE.
    """

    run_id: UUID
    pass_kind: PassKind
    hostname: str
    pid: int
    started_at: datetime
    walk_anchor_at: datetime | None = None
    ended_at: datetime | None = None
    phase: str | None = None
    progress_done: int | None = None
    progress_total: int | None = None
    progress_updated_at: datetime | None = None
    outcome: PassRunOutcome | None = None
    exit_code: int | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

_COLS = (
    "run_id, pass, hostname, pid, walk_anchor_at, started_at, ended_at, "
    "phase, progress_done, progress_total, progress_updated_at, "
    "outcome, exit_code, detail"
)


def _row_to_pass_run(row: dict) -> PassRun:
    outcome = row["outcome"]
    return PassRun(
        run_id=row["run_id"],
        pass_kind=PassKind(row["pass"]),
        hostname=row["hostname"],
        pid=row["pid"],
        walk_anchor_at=row["walk_anchor_at"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        phase=row["phase"],
        progress_done=row["progress_done"],
        progress_total=row["progress_total"],
        progress_updated_at=row["progress_updated_at"],
        outcome=PassRunOutcome(outcome) if outcome is not None else None,
        exit_code=row["exit_code"],
        detail=row["detail"],
    )


def abandoned_detail(pid: int) -> str:
    """Detail text written when a run's process no longer exists (Decision 4).

    The one place this wording is produced; the overview renders it verbatim.
    """
    return f"abandoned: pid {pid} gone"


class PassRunRepository:
    """Read/write access to the pass_runs table.

    All SQL is parameterized.

    Design 922, Decision 1: rows are written by the process that runs the
    pass. Nothing here observes another process's work — the only method that
    touches a row this process did not open is :meth:`close_abandoned`, and
    the caller must have established that those pids are gone.

    Args:
        connect: Yields a connection for the duration of one statement. A
            long-lived caller (the daemon) passes its pool's ``connection``;
            a short-lived command passes a plain connect, which costs one
            connection per write and spares it a pool's background workers.
    """

    def __init__(
        self, connect: Callable[[], AbstractContextManager[psycopg.Connection]]
    ) -> None:
        self._connect = connect

    @classmethod
    def from_pool(cls, pool: ConnectionPool) -> PassRunRepository:
        """A repository over an existing pool."""
        return cls(pool.connection)

    def insert(self, run: PassRun) -> None:
        """Insert a new run row. Open runs carry no ended_at and no outcome."""
        sql = """
            INSERT INTO pass_runs (
                run_id, pass, hostname, pid, walk_anchor_at, started_at,
                ended_at, phase, progress_done, progress_total,
                progress_updated_at, outcome, exit_code, detail
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            run.run_id,
            str(run.pass_kind),
            run.hostname,
            run.pid,
            run.walk_anchor_at,
            run.started_at,
            run.ended_at,
            run.phase,
            run.progress_done,
            run.progress_total,
            run.progress_updated_at,
            str(run.outcome) if run.outcome is not None else None,
            run.exit_code,
            run.detail,
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)

    def update_progress(
        self,
        run_id: UUID,
        *,
        phase: str | None,
        done: int | None,
        total: int | None,
        at: datetime,
    ) -> None:
        """Record how far an open run has got. Ended rows are left alone."""
        sql = """
            UPDATE pass_runs
               SET phase = %s, progress_done = %s, progress_total = %s,
                   progress_updated_at = %s
             WHERE run_id = %s AND ended_at IS NULL
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (phase, done, total, at, run_id))

    def close(
        self,
        run_id: UUID,
        *,
        ended_at: datetime,
        outcome: PassRunOutcome,
        exit_code: int | None,
        detail: str | None,
    ) -> None:
        """Close an open run. Already-ended rows are left alone."""
        sql = """
            UPDATE pass_runs
               SET ended_at = %s, outcome = %s, exit_code = %s, detail = %s
             WHERE run_id = %s AND ended_at IS NULL
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (ended_at, str(outcome), exit_code, detail, run_id))

    def open_runs(self, kind: PassKind) -> list[PassRun]:
        """Every still-open run of this kind, on any host, newest first."""
        sql = (
            f"SELECT {_COLS} FROM pass_runs "
            "WHERE pass = %s AND ended_at IS NULL "
            "ORDER BY started_at DESC"
        )
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (str(kind),))
                rows = cur.fetchall()
        return [_row_to_pass_run(row) for row in rows]

    def latest_ended(self, kind: PassKind) -> PassRun | None:
        """The most recently started ended run of this kind, or None."""
        sql = (
            f"SELECT {_COLS} FROM pass_runs "
            "WHERE pass = %s AND ended_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 1"
        )
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (str(kind),))
                row = cur.fetchone()
        return _row_to_pass_run(row) if row is not None else None

    def close_abandoned(
        self,
        kind: PassKind,
        *,
        hostname: str,
        dead_pids: Sequence[int],
        now: datetime,
    ) -> int:
        """Close open runs of this kind left behind by processes that are gone.

        Scoped to one host and an explicit pid list the caller has already
        established are dead (Decision 4): a live pid here, or any pid on
        another host, is never touched. Returns the number of rows closed.
        """
        if not dead_pids:
            return 0
        sql = """
            UPDATE pass_runs
               SET ended_at = %s, outcome = %s, detail = %s
             WHERE pass = %s AND hostname = %s AND pid = %s
               AND ended_at IS NULL
        """
        closed = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                for pid in dead_pids:
                    cur.execute(
                        sql,
                        (
                            now,
                            str(PassRunOutcome.FAILED),
                            abandoned_detail(pid),
                            str(kind),
                            hostname,
                            pid,
                        ),
                    )
                    closed += cur.rowcount
        return closed
