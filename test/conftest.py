"""Shared test fixtures for database availability."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

TEST_ADMIN_URL = os.environ.get("MT_TIMESCALE_TEST_URL", "")
"""Admin URL used to create/drop throwaway databases.

Must point at a TimescaleDB-equipped Postgres instance, e.g.
``postgresql://postgres:pw@host:5432/postgres``. Deliberately separate from
``MT_TIMESCALE_DB_URL``: tests that create and drop databases must never be
pointed at production by an unset variable defaulting to the working DB.
"""


def swap_dbname(url: str, new_db: str) -> str:
    """Return ``url`` with the path component replaced by ``new_db``."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{new_db}"))


@pytest.fixture(scope="function")
def ephemeral_db() -> Iterator[str]:
    """Create a UUID-named database, yield its URL, drop it on teardown.

    A genuinely empty database, so migration-level tests neither mutate shared
    state nor inherit another database's chunk layout. Shared at the ``test/``
    level so both the integration and load tiers can use it.
    """
    if not TEST_ADMIN_URL:
        pytest.skip("MT_TIMESCALE_TEST_URL not set")

    db_name = f"mt_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{db_name}"')

    try:
        yield swap_dbname(TEST_ADMIN_URL, db_name)
    finally:
        with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as admin:
            # Terminate live connections so DROP DATABASE doesn't block.
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


@pytest.fixture()
def migrated_db(ephemeral_db: str) -> str:
    """Ephemeral database with the full migration chain applied.

    The standard base for any test that needs real schema and may write:
    it can only name a database that did not exist before the test. Skips
    (via ``ephemeral_db``) when ``MT_TIMESCALE_TEST_URL`` is unset.
    """
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)
    return ephemeral_db


@pytest.fixture
def market_db_url() -> str:
    """PostgreSQL connection URL for the market (daily OHLCV) database.

    Reads from MT_MARKET_DB_URL environment variable.
    Skips the test if not set.
    """
    url = os.environ.get("MT_MARKET_DB_URL")
    if not url:
        pytest.skip("MT_MARKET_DB_URL not set")
    return url


@pytest.fixture
def timescale_db_url() -> str:
    """PostgreSQL connection URL for the TimescaleDB (minute data) database.

    Reads from MT_TIMESCALE_DB_URL environment variable.
    Skips the test if not set.
    """
    url = os.environ.get("MT_TIMESCALE_DB_URL")
    if not url:
        pytest.skip("MT_TIMESCALE_DB_URL not set")
    return url
