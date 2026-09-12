"""PassRunRecorder: the write side of pass_runs, safe to call from a pass.

The recorder is the only thing a pass talks to. Its contract (Design 922,
Decision 4) is that **recording never aborts a pass**: every method catches
``psycopg.Error``, logs at ERROR with the traceback, and returns. A pass that
cannot write its bookkeeping row still does its real work.

At :meth:`open` the recorder also closes rows this host left behind: open
runs of the same kind whose pid no longer exists. Liveness is checked per
pid, so a concurrently running pass — a live pid here, or any pid on another
host — is never touched.

It closes one more class of row: this process's own, opened before this
recorder existed. The pid sweep cannot reach those (the pid is live), but a
swallowed close failure leaves them open forever, and the daemon opens a new
row every cycle.
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
        # Rows this pid opened before this instant belong to an earlier
        # recorder, so closing them cannot disturb a pass of this one's.
        self._born_at = clock()

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
        self._close_stale(kind, now)
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

    def clear_walk_anchor(self, run_id: UUID | None) -> None:
        """Withdraw a run's walk-anchor claim. A None run_id is a no-op."""
        if run_id is None:
            return
        try:
            self._repo.clear_walk_anchor(run_id)
        except psycopg.Error:
            _logger.exception(
                "pass_runs: could not clear the walk anchor for run %s", run_id
            )

    def _close_stale(self, kind: PassKind, now: datetime) -> None:
        """Close rows of this kind that no live pass owns, in one read.

        Two kinds of leftover, both found in the same ``open_runs`` result so
        that opening a pass costs one extra query and not three:

        * **abandoned** — this host's rows whose pid no longer exists.
          Liveness is checked per pid, so a concurrently running pass (a live
          pid here, or any pid on another host) is never touched.
        * **superseded** — this process's own rows, started before this
          recorder existed. The pid check cannot reach these because the pid
          is live: it is ours. But the daemon opens a new row every cycle and
          :meth:`close` swallows a ``psycopg.Error`` by contract, so a
          database that went away mid-pass leaves a row nothing could ever
          close, and the overview shows one more phantom RUNNING pass every
          cycle (922 review F006).
        """
        try:
            open_runs = self._repo.open_runs(kind)
        except psycopg.Error:
            _logger.exception(
                "pass_runs: could not read open %s runs; skipping the "
                "leftover-run sweep",
                kind.value,
            )
            return
        mine = [run for run in open_runs if run.hostname == self._hostname]
        dead = [
            run.pid
            for run in mine
            if run.pid != self._pid and not self._is_alive(run.pid)
        ]
        superseded = [
            run
            for run in mine
            if run.pid == self._pid and run.started_at < self._born_at
        ]
        self._close_abandoned(kind, dead, now)
        self._close_superseded(kind, superseded, now)

    def _close_superseded(
        self, kind: PassKind, runs: list[PassRun], now: datetime
    ) -> None:
        """Close this process's own rows an earlier pass left open."""
        if not runs:
            return
        try:
            closed = self._repo.close_superseded(
                kind,
                hostname=self._hostname,
                pid=self._pid,
                before=self._born_at,
                now=now,
            )
        except psycopg.Error:
            _logger.exception(
                "pass_runs: could not close superseded %s runs", kind.value
            )
            return
        if closed:
            _logger.warning(
                "pass_runs: closed %d %s run(s) this process left open — an "
                "earlier close did not reach the database",
                closed,
                kind.value,
            )

    def _close_abandoned(
        self, kind: PassKind, dead: list[int], now: datetime
    ) -> None:
        """Close same-kind rows on this host whose process is gone."""
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
