"""Cold-start integration tests for slice 156.

Verifies that an empty TimescaleDB-equipped Postgres database can reach
the current schema using only ``mt data init`` (i.e. via
``apply_schema_migrations()``). The negative test asserts that removing
a CREATE-table migration causes a clear failure later in the chain —
this is the regression class fixed by issue #16.

Skips unless ``MT_TIMESCALE_TEST_URL`` is set; that variable must point
at an *admin* connection (e.g. the maintenance ``postgres`` database)
so the fixtures can CREATE / DROP throwaway databases. The Timescale
extension must be available on the target instance.

Each test gets a fresh UUID-named database; the fixture terminates
live connections before DROP to avoid teardown hangs.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

from manta_trading.constants import MINUTE_CAGG_CHUNK_INTERVAL
from manta_trading.market.schema.migrations.minute import (
    _MINUTE_CAGG_VIEWS,
    MINUTE_MIGRATIONS,
)
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

# The four minute caggs migrations 044/045 target. Sourced from the migration
# module so a granularity added there is automatically covered here.
MINUTE_CAGG_VIEWS: tuple[str, ...] = tuple(_MINUTE_CAGG_VIEWS)

TEST_ADMIN_URL = os.environ.get("MT_TIMESCALE_TEST_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_ADMIN_URL,
    reason=(
        "MT_TIMESCALE_TEST_URL not set — cold-start integration tests "
        "require an admin URL pointing at a TimescaleDB-equipped Postgres "
        "instance (e.g. postgresql://postgres:pw@host:5432/postgres)."
    ),
)


# ---------------------------------------------------------------------------
# Manifest the fresh schema must produce. Listed explicitly (not derived
# from MINUTE_MIGRATIONS) so a future migration that silently drops one of
# these is caught.
# ---------------------------------------------------------------------------

EXPECTED_TABLES: tuple[str, ...] = (
    "instruments",
    "provider_symbol_mapping",
    "trading_calendars",
    "trading_holidays",
    "acquisition_state",
    "backfill_state",
    "data_gaps",
    "daily_ohlcv",
    "trading_sessions",
    "splits",
    "dividends",
    "minute_ohlcv",
    "daemon_heartbeat",
)

EXPECTED_CAGGS: tuple[str, ...] = (
    "minute_5min_ohlcv",
    "minute_15min_ohlcv",
    "minute_hourly_ohlcv",
    "minute_4hour_ohlcv",
    "daily_weekly_ohlcv",
    "daily_monthly_ohlcv",
    "daily_quarterly_ohlcv",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _swap_dbname(url: str, new_db: str) -> str:
    """Return ``url`` with the path component replaced by ``new_db``."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{new_db}"))


@pytest.fixture(scope="function", autouse=True)
def _isolate_marketdb_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force migration 036 down its no-op path.

    Migration 036_copy_splits_dividends_from_marketdb reads
    MT_MARKET_DB_URL at apply time. If a developer has it set in their
    shell or .env (loaded by uv run), the migration will try to copy
    real splits/dividends rows from the live MarketDB into the
    ephemeral test database — which (a) makes the test non-hermetic
    and (b) couples test runtime to whichever MarketDB the developer
    happens to point at. Force the unset path so the test exercises
    the real cold-start migration list end-to-end without phoning
    home.
    """
    monkeypatch.delenv("MT_MARKET_DB_URL", raising=False)


@pytest.fixture(scope="function")
def ephemeral_db() -> Iterator[str]:
    """Create a UUID-named DB, yield its URL, drop it on teardown."""
    db_name = f"mt_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{db_name}"')

    yield_url = _swap_dbname(TEST_ADMIN_URL, db_name)

    try:
        yield yield_url
    finally:
        with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as admin:
            # Terminate live connections so DROP DATABASE doesn't block.
            admin.execute(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestColdStartProducesWorkingSchema:
    def test_apply_migrations_brings_schema_to_current(
        self, ephemeral_db: str
    ) -> None:
        db = TimescaleMinuteDataDB(conninfo=ephemeral_db)
        try:
            applied = db.apply_schema_migrations()
        finally:
            db.close()
        # The runner bootstraps 001_schema_migrations out-of-band and does
        # not include it in the returned list, so the count is N-1 on a
        # fresh DB. The schema_migrations row count below verifies the
        # full set actually landed.
        assert len(applied) == len(MINUTE_MIGRATIONS) - 1, (
            "expected every non-bootstrap migration applied on a fresh DB"
        )

        with psycopg.connect(ephemeral_db) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM schema_migrations")
                row = cur.fetchone()
                assert row is not None
                assert int(row[0]) == len(MINUTE_MIGRATIONS)

                # Tables present
                for tbl in EXPECTED_TABLES:
                    cur.execute(
                        "SELECT to_regclass(%s) IS NOT NULL", (f"public.{tbl}",)
                    )
                    row = cur.fetchone()
                    assert row is not None and bool(row[0]), (
                        f"table '{tbl}' missing after cold-start"
                    )

                # Cagg materialized views present
                for view in EXPECTED_CAGGS:
                    cur.execute(
                        "SELECT 1 FROM timescaledb_information.continuous_aggregates "
                        "WHERE view_name = %s",
                        (view,),
                    )
                    assert cur.fetchone() is not None, (
                        f"cagg '{view}' missing after cold-start"
                    )

                # Cagg refresh policies installed (one per cagg)
                cur.execute(
                    "SELECT COUNT(*) FROM timescaledb_information.jobs "
                    "WHERE proc_name = 'policy_refresh_continuous_aggregate'"
                )
                row = cur.fetchone()
                assert row is not None
                assert int(row[0]) == len(EXPECTED_CAGGS), (
                    "expected one refresh policy per cagg"
                )

                # Minute caggs land at the slice-163 chunk interval and have
                # columnstore enabled *from migrations alone* (044 + 045) —
                # the repair tool must never be needed on a fresh DB. Asserted
                # against the constants so changing one updates both.
                for view in MINUTE_CAGG_VIEWS:
                    cur.execute(
                        "SELECT d.time_interval "
                        "FROM timescaledb_information.continuous_aggregates ca "
                        "JOIN timescaledb_information.dimensions d "
                        "  ON d.hypertable_name = ca.materialization_hypertable_name "
                        "WHERE ca.view_name = %s",
                        (view,),
                    )
                    row = cur.fetchone()
                    assert row is not None, (
                        f"no mat-hypertable dimension found for cagg '{view}'"
                    )
                    assert row[0] == MINUTE_CAGG_CHUNK_INTERVAL, (
                        f"cagg '{view}' mat chunk_time_interval is {row[0]}, "
                        f"expected {MINUTE_CAGG_CHUNK_INTERVAL} "
                        "(migration 044 did not take effect on cold start)"
                    )

                    cur.execute(
                        "SELECT compression_enabled "
                        "FROM timescaledb_information.continuous_aggregates "
                        "WHERE view_name = %s",
                        (view,),
                    )
                    row = cur.fetchone()
                    assert row is not None and bool(row[0]), (
                        f"cagg '{view}' does not have columnstore enabled "
                        "(migration 045 did not take effect on cold start)"
                    )

                # One columnstore policy per minute cagg, with the configured
                # compress_after. Regression guard for the D1 prod bug where
                # 045 rendered an untyped `7 days` into add_columnstore_policy
                # and raised a syntax error: unit tests asserted the constant
                # but never the rendered SQL, so only execution catches it.
                cur.execute(
                    "SELECT COUNT(*) FROM timescaledb_information.jobs "
                    "WHERE proc_name = 'policy_compression' "
                    "  AND hypertable_name = ANY(%s)",
                    (list(MINUTE_CAGG_VIEWS),),
                )
                row = cur.fetchone()
                assert row is not None
                assert int(row[0]) == len(MINUTE_CAGG_VIEWS), (
                    "expected one columnstore policy per minute cagg after "
                    "cold start (migration 045)"
                )

                # data_status view returns 0 rows on an empty registry
                # (and crucially does not error out)
                cur.execute("SELECT COUNT(*) FROM data_status")
                row = cur.fetchone()
                assert row is not None and int(row[0]) == 0


class TestMigration036WithMarketDB:
    """Migration 036 copies splits/dividends from MarketDB if reachable.

    Slice 156 follow-up: a latent psycopg3-port bug used
    Connection.executemany (psycopg2-only API) instead of Cursor.
    The default cold-start test runs with MT_MARKET_DB_URL forcibly
    unset by the autouse fixture, so it never exercises this path.
    This test re-enables it via MT_MARKET_DB_URL_FOR_COLD_START_TEST
    so CI / curious developers can verify the live-MarketDB path
    actually works.
    """

    def test_apply_with_marketdb_reachable(
        self,
        ephemeral_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        market_url = os.environ.get("MT_MARKET_DB_URL_FOR_COLD_START_TEST", "")
        if not market_url:
            pytest.skip(
                "MT_MARKET_DB_URL_FOR_COLD_START_TEST not set — "
                "live-MarketDB cold-start path not exercised."
            )
        # The autouse fixture deletes MT_MARKET_DB_URL; re-set it from
        # the explicit opt-in variable for this test only.
        monkeypatch.setenv("MT_MARKET_DB_URL", market_url)

        db = TimescaleMinuteDataDB(conninfo=ephemeral_db)
        try:
            db.apply_schema_migrations()
        finally:
            db.close()

        # If MarketDB had splits/dividends, they should be present.
        # We only assert that the migration completed without raising —
        # row counts depend on the live MarketDB contents.
        with psycopg.connect(ephemeral_db) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM schema_migrations "
                    "WHERE migration_id = '036_copy_splits_dividends_from_marketdb'"
                )
                assert cur.fetchone() is not None


class TestDeletedCreateMigrationFailsClearly:
    """Negative test: removing 038_create_acquisition_state must surface a
    clear, deterministic failure when the chain reaches 019_slim_acquisition_state.
    This is the exact regression class that issue #16 reported.
    """

    def test_removing_038_breaks_019(
        self,
        ephemeral_db: str,
    ) -> None:
        from manta_trading.market.schema import migrations as migrations_pkg

        patched = [
            m
            for m in MINUTE_MIGRATIONS
            if m["id"] != "038_create_acquisition_state"
        ]
        original = migrations_pkg.TRACKS["minute"]
        migrations_pkg.TRACKS["minute"] = patched
        try:
            db = TimescaleMinuteDataDB(conninfo=ephemeral_db)
            try:
                with pytest.raises(psycopg.errors.UndefinedTable) as exc_info:
                    db.apply_schema_migrations()
            finally:
                db.close()
        finally:
            migrations_pkg.TRACKS["minute"] = original

        # Error message must mention acquisition_state so the operator
        # can act on it.
        assert "acquisition_state" in str(exc_info.value)
