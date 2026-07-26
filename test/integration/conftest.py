"""Shared fixtures for integration tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

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
    state nor inherit another database's chunk layout.
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
def instruments_clean_db():
    """Reset instruments table and slice-141 migrations to pre-rebuild state.

    This fixture is for slice-141 orchestrator tests. It:
      1. Truncates instruments and dependent tables.
      2. Drops constraints/columns added by migrations 015/016/017 so each
         test starts from the pre-141 schema.
      3. Removes 015/016/017 from schema_migrations so the orchestrator
         re-applies them.
    """
    if not TIMESCALE_URL:
        pytest.skip("MT_TIMESCALE_DB_URL not set")

    def _reset() -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            conn.execute("TRUNCATE TABLE provider_symbol_mapping, instruments RESTART IDENTITY CASCADE")
            # Roll back slice 141 schema changes so each test starts from pre-141 state
            conn.execute("ALTER TABLE instruments DROP CONSTRAINT IF EXISTS instruments_eodhd_type_check")
            conn.execute("ALTER TABLE instruments DROP CONSTRAINT IF EXISTS instruments_eodhd_exchange_check")
            conn.execute("ALTER TABLE instruments ALTER COLUMN eodhd_type DROP NOT NULL")
            conn.execute("ALTER TABLE instruments ALTER COLUMN eodhd_exchange DROP NOT NULL")
            # Re-add 'active' if it was dropped (017)
            conn.execute("ALTER TABLE instruments ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            # Mark 015/016/017 as not applied so the orchestrator re-runs them
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id IN "
                "('015_instruments_lifecycle_columns', "
                " '016_instruments_eodhd_type_not_null', "
                " '017_instruments_drop_active')"
            )
            conn.commit()

    _reset()
    yield TIMESCALE_URL
    _reset()
