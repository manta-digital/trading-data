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
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import psycopg
import pytest
from tick_support.cli import clear_tick_env
from tick_support.rows import insert_bar_ledger_row, insert_statistics_ledger_row
from tick_support.statistics_seed import seed_unit

from manta_trading.api.eodhd_account import CreditUsage
from manta_trading.cli.commands.overview import (
    CREDITS_NO_KEY,
    build_overview,
    gather,
)
from manta_trading.cli.commands.overview_futures import read_futures_sources
from manta_trading.cli.rendering.overview import render_source_lines
from manta_trading.config import Settings
from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunOutcome,
    PassRunRepository,
)
from manta_trading.data.tick import universe as universe_module
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.roll_types import utc_from_ns
from manta_trading.data.tick.universe import TICK_UNIVERSE

BAR_NS = 1_733_184_000_000_000_000  # 2024-12-03 00:00 UTC
_HOST = "manta-test"
_NOW = datetime(2026, 9, 12, 16, 10, tzinfo=UTC)


def _settings(*, api_key: str | None = "k") -> MagicMock:
    settings = MagicMock()
    settings.eodhd_api_key = api_key
    settings.minute_firing_days = (5,)
    settings.tick_db_url = None  # the 230 tick line: not configured, no connect
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
            "futures ticks",  # 230: gathered on the CLI side only
            "futures ohlcv-1m",  # 305: the production universe wants bars
            "futures statistics",  # ... and statistics
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
        # four database sources + the tick line + 305's two family lines
        assert len(facts.sources) == 7

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


class TestFuturesFamilyLines:
    """305 D7: the newest bar and statistics session beside ``futures ticks``."""

    DAY = date(2024, 12, 3)

    @pytest.fixture
    def tick_settings(
        self, migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> Settings:
        clear_tick_env(monkeypatch)
        return Settings(_env_file=None, tick_db_url=migrated_tick_db)  # type: ignore[call-arg]

    def _seed(self, tick_url: str) -> None:
        with psycopg.connect(tick_url, autocommit=True) as tick:
            bars = seed_unit(tick, TickSchema.OHLCV_1M, self.DAY)
            stats = seed_unit(tick, TickSchema.STATISTICS, self.DAY)
            insert_bar_ledger_row(tick, bars, last_event_ns=BAR_NS)
            insert_statistics_ledger_row(tick, stats, session_date=self.DAY)

    def _universe(self, monkeypatch: pytest.MonkeyPatch, *, wanted: bool) -> None:
        entry = TICK_UNIVERSE[0] if wanted else replace(TICK_UNIVERSE[0], history=())
        monkeypatch.setattr(universe_module, "TICK_UNIVERSE", (entry,))

    def test_both_lines_appear_when_a_product_wants_the_families(
        self,
        tick_settings: Settings,
        migrated_tick_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._seed(migrated_tick_db)
        self._universe(monkeypatch, wanted=True)
        bars, statistics = read_futures_sources(tick_settings)
        assert (bars.name, bars.newest) == ("futures ohlcv-1m", utc_from_ns(BAR_NS))
        assert (statistics.name, statistics.newest) == ("futures statistics", None)
        assert statistics.session_date == self.DAY
        shown = render_source_lines([statistics], _NOW)[0]
        assert shown.endswith("2024-12-03 (session)")  # a date, not a timestamp

    def test_neither_line_appears_when_no_product_wants_the_families(
        self,
        tick_settings: Settings,
        migrated_tick_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._seed(migrated_tick_db)
        self._universe(monkeypatch, wanted=False)
        assert read_futures_sources(tick_settings) == []

    def test_empty_ledgers_report_no_newest_row(
        self, tick_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._universe(monkeypatch, wanted=True)
        bars, statistics = read_futures_sources(tick_settings)
        assert (bars.newest, bars.unavailable) == (None, None)
        assert (statistics.session_date, statistics.unavailable) == (None, None)

    def test_a_tick_outage_is_each_lines_text_and_the_overview_still_returns(
        self, conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clear_tick_env(monkeypatch)
        self._universe(monkeypatch, wanted=True)
        facts = gather(conn, _settings(api_key=None), now=_NOW, hostname=_HOST)
        by_name = {source.name: source for source in facts.sources}
        for name in ("futures ohlcv-1m", "futures statistics"):
            assert by_name[name].unavailable == "not configured (MT_TICK_DB_URL unset)"
        assert build_overview(facts, pid_alive=lambda _pid: True).sources
