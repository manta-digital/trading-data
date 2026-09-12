"""Integration tests: PassRunRepository against real schema (slice 922).

Runs against a throwaway database the fixture creates and drops itself
(``migrated_db``, from ``test/conftest.py``), so nothing here can reach a
configured production database. Requires ``MT_TIMESCALE_TEST_URL``.

Covers the full lifecycle (insert, progress, close, read back) and the
abandoned-run rule from Decision 4: only the named dead pids on this host are
closed, and a live pid or a foreign host is left alone.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from psycopg_pool import ConnectionPool

from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunOutcome,
    PassRunRepository,
    abandoned_detail,
)

_HOST = "manta-test"
_T0 = datetime(2026, 9, 12, 13, 5, tzinfo=UTC)


@pytest.fixture
def pool(migrated_db: str) -> Iterator[ConnectionPool]:
    with ConnectionPool(migrated_db, min_size=1, max_size=3, open=True) as p:
        yield p


@pytest.fixture
def repo(pool: ConnectionPool) -> PassRunRepository:
    return PassRunRepository.from_pool(pool)


def _run(
    *,
    kind: PassKind = PassKind.MINUTE,
    hostname: str = _HOST,
    pid: int = 1000,
    started_at: datetime = _T0,
    walk_anchor_at: datetime | None = None,
) -> PassRun:
    return PassRun(
        run_id=uuid.uuid4(),
        pass_kind=kind,
        hostname=hostname,
        pid=pid,
        started_at=started_at,
        walk_anchor_at=walk_anchor_at,
    )


class TestLifecycle:
    def test_insert_then_open_runs_returns_it(self, repo: PassRunRepository) -> None:
        run = _run(walk_anchor_at=_T0)
        repo.insert(run)
        found = repo.open_runs(PassKind.MINUTE)
        assert [r.run_id for r in found] == [run.run_id]
        assert found[0].pass_kind is PassKind.MINUTE
        assert found[0].walk_anchor_at == _T0
        assert found[0].outcome is None
        assert found[0].ended_at is None

    def test_update_progress_is_read_back(self, repo: PassRunRepository) -> None:
        run = _run()
        repo.insert(run)
        at = _T0 + timedelta(minutes=3)
        repo.update_progress(run.run_id, phase="trailing", done=250, total=15215, at=at)
        found = repo.open_runs(PassKind.MINUTE)[0]
        assert (found.phase, found.progress_done, found.progress_total) == (
            "trailing",
            250,
            15215,
        )
        assert found.progress_updated_at == at

    def test_close_moves_the_row_to_latest_ended(self, repo: PassRunRepository) -> None:
        run = _run()
        repo.insert(run)
        ended = _T0 + timedelta(minutes=20)
        repo.close(
            run.run_id,
            ended_at=ended,
            outcome=PassRunOutcome.COMPLETE_QUOTA,
            exit_code=0,
            detail="trailing 15215/15215 · backfill 900 symbols",
        )
        assert repo.open_runs(PassKind.MINUTE) == []
        latest = repo.latest_ended(PassKind.MINUTE)
        assert latest is not None
        assert latest.run_id == run.run_id
        assert latest.outcome is PassRunOutcome.COMPLETE_QUOTA
        assert latest.exit_code == 0
        assert latest.ended_at == ended
        assert latest.detail is not None and "backfill" in latest.detail

    def test_latest_ended_is_none_before_any_run_ends(
        self, repo: PassRunRepository
    ) -> None:
        repo.insert(_run())
        assert repo.latest_ended(PassKind.MINUTE) is None

    def test_kinds_do_not_bleed_into_each_other(self, repo: PassRunRepository) -> None:
        repo.insert(_run(kind=PassKind.MINUTE))
        repo.insert(_run(kind=PassKind.DAILY, pid=1001))
        assert len(repo.open_runs(PassKind.MINUTE)) == 1
        assert len(repo.open_runs(PassKind.DAILY)) == 1
        assert repo.open_runs(PassKind.KALSHI) == []

    def test_open_runs_are_newest_first(self, repo: PassRunRepository) -> None:
        older = _run(pid=1, started_at=_T0)
        newer = _run(pid=2, started_at=_T0 + timedelta(minutes=5))
        repo.insert(older)
        repo.insert(newer)
        assert [r.pid for r in repo.open_runs(PassKind.MINUTE)] == [2, 1]

    def test_latest_ended_picks_the_newest_start(self, repo: PassRunRepository) -> None:
        for pid, start in ((1, _T0), (2, _T0 + timedelta(minutes=5))):
            run = _run(pid=pid, started_at=start)
            repo.insert(run)
            repo.close(
                run.run_id,
                ended_at=start + timedelta(minutes=1),
                outcome=PassRunOutcome.COMPLETE,
                exit_code=0,
                detail=None,
            )
        latest = repo.latest_ended(PassKind.MINUTE)
        assert latest is not None and latest.pid == 2

    def test_close_does_not_reopen_or_rewrite_an_ended_run(
        self, repo: PassRunRepository
    ) -> None:
        run = _run()
        repo.insert(run)
        first = _T0 + timedelta(minutes=10)
        repo.close(
            run.run_id,
            ended_at=first,
            outcome=PassRunOutcome.COMPLETE,
            exit_code=0,
            detail="first",
        )
        repo.close(
            run.run_id,
            ended_at=first + timedelta(minutes=5),
            outcome=PassRunOutcome.FAILED,
            exit_code=1,
            detail="second",
        )
        latest = repo.latest_ended(PassKind.MINUTE)
        assert latest is not None
        assert latest.outcome is PassRunOutcome.COMPLETE
        assert latest.detail == "first"

    def test_update_progress_ignores_an_ended_run(
        self, repo: PassRunRepository
    ) -> None:
        run = _run()
        repo.insert(run)
        repo.close(
            run.run_id,
            ended_at=_T0 + timedelta(minutes=10),
            outcome=PassRunOutcome.COMPLETE,
            exit_code=0,
            detail=None,
        )
        repo.update_progress(
            run.run_id,
            phase="late",
            done=1,
            total=2,
            at=_T0 + timedelta(minutes=11),
        )
        latest = repo.latest_ended(PassKind.MINUTE)
        assert latest is not None and latest.phase is None


class TestCloseAbandoned:
    """Decision 4: only the named dead pids on this host are closed."""

    def test_closes_the_dead_pid_with_the_abandoned_detail(
        self, repo: PassRunRepository
    ) -> None:
        dead = _run(pid=4242)
        repo.insert(dead)
        now = _T0 + timedelta(hours=1)
        assert (
            repo.close_abandoned(
                PassKind.MINUTE, hostname=_HOST, dead_pids=[4242], now=now
            )
            == 1
        )
        assert repo.open_runs(PassKind.MINUTE) == []
        latest = repo.latest_ended(PassKind.MINUTE)
        assert latest is not None
        assert latest.outcome is PassRunOutcome.FAILED
        assert latest.detail == abandoned_detail(4242)
        assert latest.ended_at == now

    def test_leaves_a_live_pid_alone(self, repo: PassRunRepository) -> None:
        repo.insert(_run(pid=4242))
        repo.insert(_run(pid=777))
        closed = repo.close_abandoned(
            PassKind.MINUTE,
            hostname=_HOST,
            dead_pids=[4242],
            now=_T0 + timedelta(hours=1),
        )
        assert closed == 1
        assert [r.pid for r in repo.open_runs(PassKind.MINUTE)] == [777]

    def test_leaves_a_foreign_host_alone(self, repo: PassRunRepository) -> None:
        repo.insert(_run(pid=4242, hostname="other-host"))
        closed = repo.close_abandoned(
            PassKind.MINUTE,
            hostname=_HOST,
            dead_pids=[4242],
            now=_T0 + timedelta(hours=1),
        )
        assert closed == 0
        assert [r.hostname for r in repo.open_runs(PassKind.MINUTE)] == ["other-host"]

    def test_leaves_another_kind_alone(self, repo: PassRunRepository) -> None:
        repo.insert(_run(kind=PassKind.DAILY, pid=4242))
        closed = repo.close_abandoned(
            PassKind.MINUTE,
            hostname=_HOST,
            dead_pids=[4242],
            now=_T0 + timedelta(hours=1),
        )
        assert closed == 0
        assert len(repo.open_runs(PassKind.DAILY)) == 1

    def test_empty_pid_list_touches_nothing(self, repo: PassRunRepository) -> None:
        repo.insert(_run(pid=4242))
        assert (
            repo.close_abandoned(PassKind.MINUTE, hostname=_HOST, dead_pids=[], now=_T0)
            == 0
        )
        assert len(repo.open_runs(PassKind.MINUTE)) == 1

    def test_closes_several_dead_pids_at_once(self, repo: PassRunRepository) -> None:
        for pid in (10, 11, 12):
            repo.insert(_run(pid=pid))
        closed = repo.close_abandoned(
            PassKind.MINUTE,
            hostname=_HOST,
            dead_pids=[10, 12],
            now=_T0 + timedelta(hours=1),
        )
        assert closed == 2
        assert [r.pid for r in repo.open_runs(PassKind.MINUTE)] == [11]
