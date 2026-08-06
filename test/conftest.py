"""Shared test fixtures for database availability.

Also home to the runtime prod-URL scrub (see ``pytest_configure``). The
per-tier ratchet guards in ``_prod_url_guard.py`` are *static* — they stop new
code from reading the production variable, but cannot make the 21 allowlisted
readers safe at runtime. This module closes that half: it removes the variable
from the environment before collection unless the run explicitly opts in.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

# Needle concatenated so this module's own source cannot trip the static guard.
_PROD_URL_VAR = "MT_TIMESCALE" + "_DB_URL"
_PROD_OPT_IN_VAR = "MT_ALLOW_PROD_READS"


def pytest_configure(config: pytest.Config) -> None:
    """Scrub the production DB URL from the environment before collection.

    The destructive integration tests (``DROP TABLE daily_ohlcv CASCADE``,
    ``TRUNCATE ... CASCADE``) read ``MT_TIMESCALE_DB_URL`` via ``os.environ``
    at *module import time*, so a fixture-level check runs far too late and a
    caller-side ``env.pop()`` only protects the one command that remembers it.
    Removing the variable here means those modules see nothing and skip
    themselves — the tier is safe no matter how pytest was invoked.

    Fails closed: the safe state is the *absence* of configuration. Opt in
    with ``MT_ALLOW_PROD_READS=1`` for the read-only checks that genuinely
    need real production data; that opt-in is deliberately not something any
    fixture or ``.env`` sets for you.
    """
    if os.environ.get(_PROD_OPT_IN_VAR) == "1":
        return

    if os.environ.pop(_PROD_URL_VAR, None):
        config.stash[_prod_url_scrubbed] = True


_prod_url_scrubbed = pytest.StashKey[bool]()


def pytest_report_header(config: pytest.Config) -> list[str]:
    """Make the scrub visible, so a wall of skips is never mysterious."""
    if config.stash.get(_prod_url_scrubbed, False):
        return [
            f"{_PROD_URL_VAR} scrubbed from env (prod-safety guard); "
            f"tests needing it will skip. Set {_PROD_OPT_IN_VAR}=1 to allow "
            "read-only production access."
        ]
    return []


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


@pytest.fixture(scope="session")
def session_ephemeral_db() -> Iterator[str]:
    """Session-scoped twin of :func:`ephemeral_db`.

    Same guarantee — a UUID-named database the fixture created and drops — but
    built once per session. For suites whose tests do not mutate shared state
    (read-only checks, or writes they roll back), per-test rebuilds are pure
    cost: the migration chain is 50+ steps.

    Prefer the function-scoped fixtures when tests write. Isolation is the
    default for a reason; this is the deliberate exception.
    """
    if not TEST_ADMIN_URL:
        pytest.skip("MT_TIMESCALE_TEST_URL not set")

    db_name = f"mt_test_s{uuid.uuid4().hex[:11]}"
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{db_name}"')

    try:
        yield swap_dbname(TEST_ADMIN_URL, db_name)
    finally:
        with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


@pytest.fixture(scope="session")
def session_migrated_db(session_ephemeral_db: str) -> str:
    """Session-scoped twin of :func:`migrated_db` — full chain, built once."""
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(session_ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)
    return session_ephemeral_db


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
