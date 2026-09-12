"""Unit tests: PassRunRecorder (slice 922).

Two contracts are under test:

1. Decision 4's abandoned-run rule — at ``open`` the recorder closes open
   rows of the same kind on this host whose pid is gone, and touches nothing
   else (a live pid, its own pid, a foreign host, another kind).
2. The never-raises contract — a repository failure is logged at ERROR and
   swallowed, because bookkeeping must not abort the pass that is doing the
   real work.

The repository is a fake: these tests are about the recorder's decisions,
not SQL. ``test/integration/test_pass_run_repository.py`` covers the SQL.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import psycopg

from manta_trading.data.acquisition.daemon.pass_run_recorder import (
    PassRunRecorder,
    pid_is_alive,
)
from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunOutcome,
)

_HOST = "manta-test"
_MY_PID = 500
_T0 = datetime(2026, 9, 12, 13, 5, tzinfo=UTC)


class FakeRepository:
    """Records calls; raises on the methods named in ``fail_on``."""

    def __init__(self, open_runs: list[PassRun] | None = None) -> None:
        self._open_runs = open_runs or []
        self.inserted: list[PassRun] = []
        self.progress_calls: list[tuple] = []
        self.close_calls: list[tuple] = []
        self.abandoned_calls: list[tuple] = []
        self.fail_on: set[str] = set()

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail_on:
            raise psycopg.OperationalError(f"fake failure in {name}")

    def insert(self, run: PassRun) -> None:
        self._maybe_fail("insert")
        self.inserted.append(run)

    def update_progress(self, run_id, *, phase, done, total, at) -> None:
        self._maybe_fail("update_progress")
        self.progress_calls.append((run_id, phase, done, total, at))

    def close(self, run_id, *, ended_at, outcome, exit_code, detail) -> None:
        self._maybe_fail("close")
        self.close_calls.append((run_id, ended_at, outcome, exit_code, detail))

    def open_runs(self, kind: PassKind) -> list[PassRun]:
        self._maybe_fail("open_runs")
        return [r for r in self._open_runs if r.pass_kind is kind]

    def latest_ended(self, kind: PassKind) -> PassRun | None:
        self._maybe_fail("latest_ended")
        return None

    def close_abandoned(self, kind, *, hostname, dead_pids, now) -> int:
        self._maybe_fail("close_abandoned")
        self.abandoned_calls.append((kind, hostname, list(dead_pids), now))
        return len(dead_pids)


def _open_run(
    *,
    pid: int,
    hostname: str = _HOST,
    kind: PassKind = PassKind.MINUTE,
) -> PassRun:
    return PassRun(
        run_id=uuid.uuid4(),
        pass_kind=kind,
        hostname=hostname,
        pid=pid,
        started_at=_T0 - timedelta(hours=2),
    )


def _recorder(
    repo: FakeRepository,
    *,
    dead: set[int] | None = None,
    now: datetime = _T0,
) -> PassRunRecorder:
    dead_pids = dead or set()
    return PassRunRecorder(
        repo,  # type: ignore[arg-type]
        lambda: now,
        hostname=_HOST,
        pid=_MY_PID,
        is_alive=lambda pid: pid not in dead_pids,
    )


class TestPidIsAlive:
    def test_own_pid_is_alive(self) -> None:
        import os

        assert pid_is_alive(os.getpid()) is True

    def test_absent_pid_is_not_alive(self) -> None:
        assert pid_is_alive(999_999) is False


class TestOpen:
    def test_inserts_an_open_row_with_the_anchor(self) -> None:
        repo = FakeRepository()
        run_id = _recorder(repo).open(PassKind.MINUTE, walk_anchor_at=_T0)
        assert len(repo.inserted) == 1
        row = repo.inserted[0]
        assert run_id == row.run_id
        assert row.pass_kind is PassKind.MINUTE
        assert row.hostname == _HOST
        assert row.pid == _MY_PID
        assert row.started_at == _T0
        assert row.walk_anchor_at == _T0
        assert row.ended_at is None
        assert row.outcome is None

    def test_anchor_defaults_to_none(self) -> None:
        repo = FakeRepository()
        _recorder(repo).open(PassKind.KALSHI)
        assert repo.inserted[0].walk_anchor_at is None

    def test_returns_none_when_the_insert_fails(self, caplog) -> None:
        repo = FakeRepository()
        repo.fail_on = {"insert"}
        with caplog.at_level(logging.ERROR):
            run_id = _recorder(repo).open(PassKind.MINUTE)
        assert run_id is None
        assert any(r.levelno == logging.ERROR for r in caplog.records)


class TestAbandonedSweep:
    def test_closes_a_dead_pid_on_this_host(self) -> None:
        repo = FakeRepository([_open_run(pid=4242)])
        _recorder(repo, dead={4242}).open(PassKind.MINUTE)
        assert len(repo.abandoned_calls) == 1
        kind, hostname, pids, now = repo.abandoned_calls[0]
        assert (kind, hostname, pids, now) == (PassKind.MINUTE, _HOST, [4242], _T0)

    def test_leaves_a_live_pid_alone(self) -> None:
        repo = FakeRepository([_open_run(pid=4242)])
        _recorder(repo, dead=set()).open(PassKind.MINUTE)
        assert repo.abandoned_calls == []

    def test_leaves_a_foreign_host_alone(self) -> None:
        repo = FakeRepository([_open_run(pid=4242, hostname="other-host")])
        _recorder(repo, dead={4242}).open(PassKind.MINUTE)
        assert repo.abandoned_calls == []

    def test_never_closes_its_own_pid(self) -> None:
        """A recorder must not sweep the row it is about to write beside."""
        repo = FakeRepository([_open_run(pid=_MY_PID)])
        _recorder(repo, dead={_MY_PID}).open(PassKind.MINUTE)
        assert repo.abandoned_calls == []

    def test_sweeps_only_the_kind_being_opened(self) -> None:
        repo = FakeRepository(
            [
                _open_run(pid=10, kind=PassKind.MINUTE),
                _open_run(pid=11, kind=PassKind.DAILY),
            ]
        )
        _recorder(repo, dead={10, 11}).open(PassKind.MINUTE)
        assert [c[2] for c in repo.abandoned_calls] == [[10]]

    def test_closes_several_dead_pids_at_once(self) -> None:
        repo = FakeRepository([_open_run(pid=10), _open_run(pid=11), _open_run(pid=12)])
        _recorder(repo, dead={10, 12}).open(PassKind.MINUTE)
        assert repo.abandoned_calls[0][2] == [10, 12]

    def test_still_opens_when_the_sweep_read_fails(self, caplog) -> None:
        repo = FakeRepository([_open_run(pid=4242)])
        repo.fail_on = {"open_runs"}
        with caplog.at_level(logging.ERROR):
            run_id = _recorder(repo, dead={4242}).open(PassKind.MINUTE)
        assert run_id is not None
        assert len(repo.inserted) == 1
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_still_opens_when_the_sweep_write_fails(self, caplog) -> None:
        repo = FakeRepository([_open_run(pid=4242)])
        repo.fail_on = {"close_abandoned"}
        with caplog.at_level(logging.ERROR):
            run_id = _recorder(repo, dead={4242}).open(PassKind.MINUTE)
        assert run_id is not None
        assert len(repo.inserted) == 1
        assert any(r.levelno == logging.ERROR for r in caplog.records)


class TestProgress:
    def test_delegates_with_the_clock_time(self) -> None:
        repo = FakeRepository()
        rec = _recorder(repo)
        run_id = rec.open(PassKind.MINUTE)
        rec.progress(run_id, phase="trailing", done=250, total=15215)
        assert repo.progress_calls == [(run_id, "trailing", 250, 15215, _T0)]

    def test_a_none_run_id_is_a_noop(self) -> None:
        repo = FakeRepository()
        _recorder(repo).progress(None, phase="trailing", done=1, total=2)
        assert repo.progress_calls == []

    def test_a_repository_error_is_logged_and_swallowed(self, caplog) -> None:
        repo = FakeRepository()
        rec = _recorder(repo)
        run_id = rec.open(PassKind.MINUTE)
        repo.fail_on = {"update_progress"}
        with caplog.at_level(logging.ERROR):
            rec.progress(run_id, phase="trailing", done=1, total=2)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors and errors[0].exc_info is not None


class TestClose:
    def test_delegates_with_the_clock_time(self) -> None:
        repo = FakeRepository()
        rec = _recorder(repo)
        run_id = rec.open(PassKind.MINUTE)
        rec.close(
            run_id,
            outcome=PassRunOutcome.COMPLETE_QUOTA,
            exit_code=0,
            detail="trailing 15215/15215",
        )
        assert repo.close_calls == [
            (run_id, _T0, PassRunOutcome.COMPLETE_QUOTA, 0, "trailing 15215/15215")
        ]

    def test_a_none_run_id_is_a_noop(self) -> None:
        repo = FakeRepository()
        _recorder(repo).close(None, outcome=PassRunOutcome.COMPLETE)
        assert repo.close_calls == []

    def test_a_repository_error_is_logged_and_swallowed(self, caplog) -> None:
        repo = FakeRepository()
        rec = _recorder(repo)
        run_id = rec.open(PassKind.MINUTE)
        repo.fail_on = {"close"}
        with caplog.at_level(logging.ERROR):
            rec.close(run_id, outcome=PassRunOutcome.FAILED, exit_code=1)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors and errors[0].exc_info is not None


class TestDefaults:
    def test_hostname_and_pid_default_to_this_process(self) -> None:
        import os
        import socket

        repo = FakeRepository()
        rec = PassRunRecorder(repo, lambda: _T0)  # type: ignore[arg-type]
        assert rec.hostname == socket.gethostname()
        assert rec.pid == os.getpid()
