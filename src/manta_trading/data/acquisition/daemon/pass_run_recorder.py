"""PassRunRecorder: the write side of pass_runs, safe to call from a pass.

The recorder is the only thing a pass talks to. Its contract (Design 922,
Decision 4) is that **recording never aborts a pass**: every method catches
``psycopg.Error``, logs at ERROR with the traceback, and returns. A pass that
cannot write its bookkeeping row still does its real work.

At :meth:`open` the recorder also closes rows this host left behind: open
runs of the same kind whose pid no longer exists. Liveness is checked per
pid, so a concurrently running pass — a live pid here, or any pid on another
host — is never touched.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import psycopg

from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunOutcome,
    PassRunRepository,
)
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


def pid_is_alive(pid: int) -> bool:
    """Return True iff a process with this pid exists on this host.

    ``os.kill(pid, 0)`` sends no signal; it only performs the existence and
    permission check. ``PermissionError`` means the process exists but is
    owned by someone else, which still counts as alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class PassRunRecorder:
    """Records one pass's lifecycle into pass_runs, never raising.

    Args:
        repository: Where rows are written.
        clock: Returns the current time; injected so tests need no patching.
        hostname: This host's name. Defaults to ``socket.gethostname()``.
        pid: This process's id. Defaults to ``os.getpid()``.
        is_alive: Pid-liveness predicate. Defaults to :func:`pid_is_alive`.
    """

    def __init__(
        self,
        repository: PassRunRepository,
        clock: Callable[[], datetime],
        *,
        hostname: str | None = None,
        pid: int | None = None,
        is_alive: Callable[[int], bool] = pid_is_alive,
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._hostname = hostname if hostname is not None else socket.gethostname()
        self._pid = pid if pid is not None else os.getpid()
        self._is_alive = is_alive

    @property
    def hostname(self) -> str:
        return self._hostname

    @property
    def pid(self) -> int:
        return self._pid

    def open(
        self, kind: PassKind, *, walk_anchor_at: datetime | None = None
    ) -> UUID | None:
        """Close this host's abandoned runs of this kind, then open a new row.

        Args:
            kind: Which pass is starting.
            walk_anchor_at: Set only when this run will attempt every active
                symbol; the data_status view measures staleness from the
                newest such anchor.

        Returns:
            The new run's id, or None if the row could not be written — in
            which case every later call for that run is a no-op.
        """
        now = self._clock()
        self._close_abandoned(kind, now)
        run = PassRun(
            run_id=uuid.uuid4(),
            pass_kind=kind,
            hostname=self._hostname,
            pid=self._pid,
            started_at=now,
            walk_anchor_at=walk_anchor_at,
        )
        try:
            self._repo.insert(run)
        except psycopg.Error:
            _logger.exception(
                "pass_runs: could not open a %s run; the pass continues unrecorded",
                kind.value,
            )
            return None
        return run.run_id

    def progress(
        self,
        run_id: UUID | None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        """Record how far the run has got. A None run_id is a no-op."""
        if run_id is None:
            return
        try:
            self._repo.update_progress(
                run_id, phase=phase, done=done, total=total, at=self._clock()
            )
        except psycopg.Error:
            _logger.exception("pass_runs: could not record progress for run %s", run_id)

    def close(
        self,
        run_id: UUID | None,
        *,
        outcome: PassRunOutcome,
        exit_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        """Close the run with its outcome. A None run_id is a no-op."""
        if run_id is None:
            return
        try:
            self._repo.close(
                run_id,
                ended_at=self._clock(),
                outcome=outcome,
                exit_code=exit_code,
                detail=detail,
            )
        except psycopg.Error:
            _logger.exception("pass_runs: could not close run %s", run_id)

    def _close_abandoned(self, kind: PassKind, now: datetime) -> None:
        """Close same-kind rows on this host whose process is gone."""
        try:
            open_runs = self._repo.open_runs(kind)
        except psycopg.Error:
            _logger.exception(
                "pass_runs: could not read open %s runs; skipping the "
                "abandoned-run sweep",
                kind.value,
            )
            return
        dead = [
            run.pid
            for run in open_runs
            if run.hostname == self._hostname
            and run.pid != self._pid
            and not self._is_alive(run.pid)
        ]
        if not dead:
            return
        try:
            closed = self._repo.close_abandoned(
                kind, hostname=self._hostname, dead_pids=dead, now=now
            )
        except psycopg.Error:
            _logger.exception(
                "pass_runs: could not close abandoned %s runs %s", kind.value, dead
            )
            return
        if closed:
            _logger.warning(
                "pass_runs: closed %d abandoned %s run(s) from dead pid(s) %s",
                closed,
                kind.value,
                dead,
            )
