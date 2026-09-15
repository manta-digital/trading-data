"""Integration: the operations routes against a real database (slice 189).

Every database here is one the fixture created — a UUID-named throwaway from
``ephemeral_db``. The production URL is never read.

The test that earns this file is :class:`TestPre055Degradation`: a database
predating migration 055 must answer 200 with empty pass state, not 500.
922's reader already degrades that way, and the point is proving the API
inherited it rather than re-deciding it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from manta_trading.api_server.app import create_app
from manta_trading.cli.commands.overview import gather, gather_db_facts
from manta_trading.cli.overview_types import CREDITS_NO_KEY
from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunOutcome,
    PassRunRepository,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

NOW = datetime(2026, 9, 13, 14, 22, 3, tzinfo=UTC)
HOST = "manta9000"
PASS_RUNS_MIGRATION = "055_create_pass_runs"
PASS_RUNS_TABLE = "pass_runs"


def _client(db_url: str) -> TestClient:
    """A TestClient over an app pointed at one throwaway database.

    ``create_app(db_url=…)`` is the 187 D9 seam: the URL is an explicit
    parameter precisely so a test can never reach production through the
    environment.
    """
    return TestClient(create_app(db_url=db_url))


def _open_run(kind: PassKind = PassKind.MINUTE) -> PassRun:
    return PassRun(
        run_id=uuid.uuid4(),
        pass_kind=kind,
        hostname=HOST,
        pid=38214,
        started_at=NOW - timedelta(minutes=17),
        phase="trailing",
        progress_done=412,
        progress_total=1100,
        progress_updated_at=NOW - timedelta(seconds=5),
    )


@pytest.fixture
def repo(migrated_db: str) -> Iterator[PassRunRepository]:
    with ConnectionPool(migrated_db, min_size=1, max_size=2) as pool:
        yield PassRunRepository.from_pool(pool)


class TestBothRoutesAnswer:
    def test_overview_is_200_on_a_migrated_database(self, migrated_db: str) -> None:
        with _client(migrated_db) as client:
            response = client.get("/api/v1/overview")

        assert response.status_code == 200
        body = response.json()
        assert {line["pass"] for line in body["passes"]} == {
            kind.value for kind in PassKind
        }
        # Nothing has run in a fresh database, so every pass is empty.
        assert all(line["running"] == [] for line in body["passes"])
        assert all(line["last_run"] is None for line in body["passes"])

    def test_credits_is_200_without_a_key(self, migrated_db: str) -> None:
        """No key configured is a setting, not a fault (D6/SC6).

        The key is cleared **on the resolved settings object**, not through
        the environment. ``Settings`` loads ``.env``, so a ``monkeypatch.delenv``
        leaves a real key in place and the request goes to EODHD and spends
        quota — which is what an earlier version of this test did. Overriding
        what the route actually reads is the only way to keep this tier off
        the network.
        """
        app = create_app(db_url=migrated_db)
        with TestClient(app) as client:
            app.state.settings = _StubSettings(eodhd_api_key=None)
            response = client.get("/api/v1/credits")

        assert response.status_code == 200
        assert response.json() == {"credits": None, "error": CREDITS_NO_KEY}

    def test_this_tier_never_calls_eodhd(self, migrated_db: str) -> None:
        """The credits route is the API's only outbound network path, and an
        integration run must not spend production quota to exercise it. Pinned
        here because the failure is silent: a live call returns a plausible
        body and the test passes while the quota drops."""
        app = create_app(db_url=migrated_db)
        with TestClient(app) as client:
            app.state.settings = _StubSettings(eodhd_api_key=None)
            body = client.get("/api/v1/credits").json()

        assert body["credits"] is None


class TestRunningAndLastRun:
    """An open row appears as running; closing it moves it to last_run."""

    def test_an_open_row_appears_in_running(
        self, migrated_db: str, repo: PassRunRepository
    ) -> None:
        run = _open_run()
        repo.insert(run)

        with _client(migrated_db) as client:
            body = client.get("/api/v1/overview").json()

        line = next(
            entry for entry in body["passes"] if entry["pass"] == PassKind.MINUTE.value
        )
        assert len(line["running"]) == 1
        row = line["running"][0]
        assert row["phase"] == "trailing"
        assert row["done"] == 412
        assert row["total"] == 1100
        assert row["hostname"] == HOST
        assert row["pid"] == 38214
        assert line["last_run"] is None
        # D4/SC5: no abandonment judgment travels over HTTP.
        assert "abandoned" not in row

    def test_closing_it_moves_it_to_last_run(
        self, migrated_db: str, repo: PassRunRepository
    ) -> None:
        run = _open_run()
        repo.insert(run)
        repo.close(
            run.run_id,
            ended_at=NOW,
            outcome=PassRunOutcome.COMPLETE_QUOTA,
            exit_code=0,
            detail="1,100 symbols; stopped at provider quota",
        )

        with _client(migrated_db) as client:
            body = client.get("/api/v1/overview").json()

        line = next(
            entry for entry in body["passes"] if entry["pass"] == PassKind.MINUTE.value
        )
        assert line["running"] == []
        assert line["last_run"]["outcome"] == PassRunOutcome.COMPLETE_QUOTA.value
        assert line["last_run"]["exit_code"] == 0
        assert line["last_run"]["detail"] == "1,100 symbols; stopped at provider quota"


@pytest.fixture
def db_without_pass_runs(ephemeral_db: str) -> str:
    """A database whose ``pass_runs`` table is absent (SC4).

    **Built by dropping the table, which is the task's documented fallback,
    because slicing the chain proved impossible — not merely awkward.**

    The preferred approach was to apply every migration except
    ``055_create_pass_runs``. That cannot work here: ``MINUTE_MIGRATIONS`` is
    ordered by dependency rather than by number, 055 sits at index 25, and
    ``056_data_status_open_gaps_and_walk_anchor`` — which comes after it —
    defines the ``data_status`` view with

        SELECT pass, MAX(walk_anchor_at) AS anchor FROM pass_runs …

    so omitting 055 makes 056 fail with ``UndefinedTable`` and no chain
    applies at all. (Verified: the omit-the-migration fixture errors in
    exactly that way.) Truncating the list at index 25 instead would drop
    ``data_status`` and every source table, and the test could then not tell
    "no pass rows" from "nothing at all" — which is the distinction this
    class exists to make.

    The drop targets **only** the UUID-named database ``ephemeral_db`` created
    for this test and drops on teardown. It names no other database, and the
    production URL is never read.

    The one caveat this approach carries: ``schema_migrations`` still records
    055 as applied, so the simulated state is "the table went away" rather
    than "055 was never run". For what is under test — whether the route
    degrades when the relation is missing — the two are the same state, since
    922's reader branches on the ``UndefinedTable`` error and not on the
    bookkeeping.
    """
    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)

    with psycopg.connect(ephemeral_db, autocommit=True) as conn:
        # CASCADE: 056's data_status view depends on the table. Dropping the
        # view with it is correct for this simulation — a database with no
        # pass_runs has no walk-anchor-based data_status either.
        conn.execute(f"DROP TABLE {PASS_RUNS_TABLE} CASCADE")
    return ephemeral_db


class TestPre055Degradation:
    """SC4: a database without ``pass_runs`` is a 200, not a 500."""

    def test_the_table_really_is_absent(self, db_without_pass_runs: str) -> None:
        """Guards the fixture itself: a test that passed because the table
        existed would prove nothing at all."""
        with psycopg.connect(db_without_pass_runs) as conn:
            missing = conn.execute("SELECT to_regclass('pass_runs')").fetchone()
            assert missing is not None and missing[0] is None

            # ...and the source tables are still there, or "no pass rows" and
            # "nothing at all" would be indistinguishable below.
            present = conn.execute(
                "SELECT to_regclass('minute_ohlcv'), to_regclass('daily_ohlcv')"
            ).fetchone()
            assert present is not None and all(present)

    def test_overview_degrades_to_empty_pass_state(
        self, db_without_pass_runs: str
    ) -> None:
        with _client(db_without_pass_runs) as client:
            response = client.get("/api/v1/overview")

        assert response.status_code == 200, response.text
        body = response.json()
        # Every pass kind is still reported, each one empty.
        assert {line["pass"] for line in body["passes"]} == {
            kind.value for kind in PassKind
        }
        assert all(line["running"] == [] for line in body["passes"])
        assert all(line["last_run"] is None for line in body["passes"])

    def test_the_sources_block_is_still_populated(
        self, db_without_pass_runs: str
    ) -> None:
        """The rest of the screen is still true — the reason 922's `_read`
        degrades instead of raising."""
        with _client(db_without_pass_runs) as client:
            body = client.get("/api/v1/overview").json()

        assert [source["name"] for source in body["sources"]] == [
            "minute bars",
            "daily bars",
            "kalshi candles",
            "kalshi trades",
        ]


class TestTheSplitGuard:
    """D3: the two readers must not drift apart.

    This fails the moment a future change adds a database read to ``gather``
    alone — the one way the screen and the endpoint could come to disagree.
    """

    def test_both_readers_return_equal_db_facts(
        self, migrated_db: str, repo: PassRunRepository
    ) -> None:
        repo.insert(_open_run())
        settings = _StubSettings(eodhd_api_key="stub-key-never-sent")

        with psycopg.connect(migrated_db) as conn:
            db_only = gather_db_facts(conn, settings, now=NOW, hostname=HOST)
            both = gather(
                conn,
                settings,
                now=NOW,
                hostname=HOST,
                # Stubbed: this assertion is about the database fields, and a
                # live call would spend quota to prove nothing about them.
                fetch_credits=lambda _key: None,
            )

        assert db_only.open_runs == both.open_runs
        assert db_only.latest_ended == both.latest_ended
        assert db_only.sources == both.sources
        assert db_only.now == both.now
        assert db_only.hostname == both.hostname
        assert db_only.minute_firing_days == both.minute_firing_days

    def test_only_gather_carries_the_credit_fields(
        self, migrated_db: str
    ) -> None:
        settings = _StubSettings(eodhd_api_key="stub-key-never-sent")

        with psycopg.connect(migrated_db) as conn:
            db_only = gather_db_facts(conn, settings, now=NOW, hostname=HOST)
            both = gather(
                conn,
                settings,
                now=NOW,
                hostname=HOST,
                fetch_credits=lambda _key: _CREDITS,
            )

        assert db_only.credits is None
        assert db_only.credits_error is None
        assert both.credits is _CREDITS


class _StubSettings:
    """Only the two attributes the operations routes read.

    ``eodhd_api_key`` defaults to ``None`` so the default is the one that
    cannot reach the network: a stub that carried a plausible key would make
    an accidental live call the easy mistake.
    """

    minute_firing_days = (0, 3)

    def __init__(self, *, eodhd_api_key: str | None = None) -> None:
        self.eodhd_api_key = eodhd_api_key


_CREDITS = object()
