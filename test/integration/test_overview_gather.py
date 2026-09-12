"""Integration tests: ``gather`` reads real rows (slice 922, Task 4.5).

``build_overview`` is covered by unit tests; what needs a database is the
reading itself — that the pass_runs queries find the rows each kind wrote,
and that a source table the database does not have is reported as empty
rather than crashing the command.

That last case is not hypothetical: a database that has never run the Kalshi
migration track still has a minute universe worth reporting on, and an
operator running the overview there should get an answer.

Requires ``MT_TIMESCALE_TEST_URL``; the fixture creates and drops its own
database.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import psycopg
import pytest

from manta_trading.api.eodhd_account import CreditUsage
from manta_trading.cli.commands.overview import (
    CREDITS_NO_KEY,
    build_overview,
    gather,
)
from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunOutcome,
    PassRunRepository,
)

_HOST = "manta-test"
_NOW = datetime(2026, 9, 12, 16, 10, tzinfo=UTC)


def _settings(*, api_key: str | None = "k") -> MagicMock:
    settings = MagicMock()
    settings.eodhd_api_key = api_key
    settings.minute_firing_days = (5,)
    return settings


@pytest.fixture
def conn(migrated_db: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(migrated_db) as connection:
        yield connection


@pytest.fixture
def repo(conn: psycopg.Connection) -> PassRunRepository:
    from contextlib import contextmanager

    @contextmanager
    def _borrow() -> Iterator[psycopg.Connection]:
        yield conn

    return PassRunRepository(_borrow)


def _seed_ended(
    repo: PassRunRepository, kind: PassKind, *, detail: str | None = None
) -> PassRun:
    run = PassRun(
        run_id=uuid.uuid4(),
        pass_kind=kind,
        hostname=_HOST,
        pid=1,
        started_at=_NOW - timedelta(hours=2),
    )
    repo.insert(run)
    repo.close(
        run.run_id,
        ended_at=_NOW - timedelta(hours=1),
        outcome=PassRunOutcome.COMPLETE,
        exit_code=0,
        detail=detail,
    )
    return run


class TestGatherReadsEveryKind:
    def test_an_ended_run_of_each_kind_is_found(
        self, conn: psycopg.Connection, repo: PassRunRepository
    ) -> None:
        for kind in PassKind:
            _seed_ended(repo, kind, detail=f"{kind.value} detail")
        facts = gather(
            conn,
            _settings(api_key=None),
            now=_NOW,
            hostname=_HOST,
        )
        for kind in PassKind:
            latest = facts.latest_ended[kind]
            assert latest is not None, kind
            assert latest.detail == f"{kind.value} detail"

    def test_an_open_run_is_found(
        self, conn: psycopg.Connection, repo: PassRunRepository
    ) -> None:
        repo.insert(
            PassRun(
                run_id=uuid.uuid4(),
                pass_kind=PassKind.MINUTE,
                hostname=_HOST,
                pid=4242,
                started_at=_NOW - timedelta(minutes=5),
                phase="trailing",
                progress_done=250,
                progress_total=15_215,
            )
        )
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        assert len(facts.open_runs[PassKind.MINUTE]) == 1
        row = facts.open_runs[PassKind.MINUTE][0]
        assert (row.phase, row.progress_done, row.progress_total) == (
            "trailing",
            250,
            15_215,
        )

    def test_an_empty_database_reports_every_kind_as_never_run(
        self, conn: psycopg.Connection
    ) -> None:
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        assert all(facts.latest_ended[kind] is None for kind in PassKind)
        assert all(facts.open_runs[kind] == [] for kind in PassKind)

    def test_the_health_and_universe_lines_come_from_their_rows(
        self, conn: psycopg.Connection, repo: PassRunRepository
    ) -> None:
        _seed_ended(repo, PassKind.HEALTH, detail="healthy")
        _seed_ended(repo, PassKind.ACCOUNTING, detail="minute universe: 1/2")
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        overview = build_overview(facts, pid_alive=lambda _pid: True)
        assert overview.health_verdict == "healthy"
        assert overview.universe == "minute universe: 1/2"


class TestGatherReadsSources:
    def test_every_source_is_reported(self, conn: psycopg.Connection) -> None:
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        assert [source.name for source in facts.sources] == [
            "minute bars",
            "daily bars",
            "kalshi candles",
            "kalshi trades",
        ]

    def test_an_empty_table_reports_no_newest_row(
        self, conn: psycopg.Connection
    ) -> None:
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        assert all(source.newest is None for source in facts.sources)

    def test_a_missing_kalshi_schema_does_not_break_the_overview(
        self, conn: psycopg.Connection
    ) -> None:
        """The minute track alone is a valid database, and the overview must
        still answer on one — the Kalshi tables live in their own track."""
        kalshi = [
            source
            for source in gather(
                conn, _settings(api_key=None), now=_NOW, hostname=_HOST
            ).sources
            if source.name.startswith("kalshi")
        ]
        assert len(kalshi) == 2
        assert all(source.newest is None for source in kalshi)

    def test_a_seeded_minute_bar_is_read_back(self, conn: psycopg.Connection) -> None:
        newest = datetime(2026, 9, 11, 20, 0, tzinfo=UTC)
        # minute_ohlcv carries no foreign key to instruments, so a bar needs
        # no reference row.
        conn.execute(
            "INSERT INTO minute_ohlcv "
            "(symbol, time, open, high, low, close, volume) "
            "VALUES (%s, %s, 1, 1, 1, 1, 1)",
            ("TESTSYM", newest),
        )
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        minute = next(s for s in facts.sources if s.name == "minute bars")
        assert minute.newest == newest


class TestCreditsAreGuarded:
    """The overview reports; a provider that will not answer is a line."""

    def test_a_missing_key_is_reported_without_a_call(
        self, conn: psycopg.Connection
    ) -> None:
        called: list[str] = []
        facts = gather(
            conn,
            _settings(api_key=None),
            now=_NOW,
            hostname=_HOST,
            fetch_credits=lambda key: called.append(key),
        )
        assert called == []
        assert facts.credits_error == CREDITS_NO_KEY

    def test_a_successful_call_is_carried(self, conn: psycopg.Connection) -> None:
        facts = gather(
            conn,
            _settings(),
            now=_NOW,
            hostname=_HOST,
            fetch_credits=lambda _key: CreditUsage(1, 2, 3),
        )
        assert facts.credits == CreditUsage(1, 2, 3)
        assert facts.credits_error is None

    def test_a_failing_call_becomes_a_line_not_an_exception(
        self, conn: psycopg.Connection
    ) -> None:
        def _boom(_key: str) -> CreditUsage:
            raise RuntimeError("timed out")

        facts = gather(conn, _settings(), now=_NOW, hostname=_HOST, fetch_credits=_boom)
        assert facts.credits is None
        assert facts.credits_error is not None
        assert "timed out" in facts.credits_error


class TestAPrePassRunsDatabase:
    """The overview must answer on a database that predates migration 055.

    An operator who has just pulled and not yet migrated should see a screen
    saying nothing has run, not a traceback — the sources, credits and
    cadences are still true, and the warning names the fix.
    """

    def test_the_screen_is_still_produced(self, conn: psycopg.Connection) -> None:
        conn.execute("DROP TABLE pass_runs CASCADE")
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        overview = build_overview(facts, pid_alive=lambda _pid: True)
        assert all(line.last is None for line in overview.passes)
        assert all(line.running == () for line in overview.passes)

    def test_the_source_reads_still_work_afterwards(
        self, conn: psycopg.Connection
    ) -> None:
        """The rollback is what makes this true: without it the poisoned
        transaction would fail every later read and lose the whole screen."""
        conn.execute("DROP TABLE pass_runs CASCADE")
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        assert len(facts.sources) == 4

    def test_it_warns_once_not_once_per_read(
        self, conn: psycopg.Connection, caplog
    ) -> None:
        import logging

        conn.execute("DROP TABLE pass_runs CASCADE")
        with caplog.at_level(logging.WARNING):
            gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        warnings = [
            r for r in caplog.records if "pass_runs does not exist" in r.message
        ]
        assert len(warnings) == 1
        assert "migrate apply" in warnings[0].message
