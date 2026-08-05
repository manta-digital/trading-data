"""Shared fixtures for integration tests.

The ``ephemeral_db`` fixture lives in ``test/conftest.py`` (shared with the
load tier); only integration-specific fixtures belong here.

This module must never read the production DB URL. On 2026-08-04 the previous
``instruments_clean_db`` — which connected to ``MT_TIMESCALE_DB_URL`` directly —
truncated six production tables when a test runner injected a whole ``.env``
into the environment. A destructive-by-design fixture may only target a
database it created itself; both fixtures below can only name the throwaway
database ``ephemeral_db`` just minted.
``test_prod_url_guard.py`` enforces the no-new-prod-URL-reads ratchet for the
whole tier.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations


@pytest.fixture()
def migrated_db(ephemeral_db: str) -> str:
    """Ephemeral database with the full migration chain applied.

    Skips (via ``ephemeral_db``) when ``MT_TIMESCALE_TEST_URL`` is unset.
    """
    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)
    return ephemeral_db


@pytest.fixture()
def instruments_clean_db(migrated_db: str) -> str:
    """Ephemeral DB rolled back to the pre-slice-141 instruments state.

    For slice-141 orchestrator tests. Rolls the freshly-migrated throwaway
    database back to pre-141: drops the 015/016/017 constraints/columns and
    removes those ledger rows so the orchestrator re-applies them. No reset
    on teardown — the database is dropped.
    """
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "TRUNCATE TABLE provider_symbol_mapping, instruments "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute(
            "ALTER TABLE instruments DROP CONSTRAINT IF EXISTS "
            "instruments_eodhd_type_check"
        )
        conn.execute(
            "ALTER TABLE instruments DROP CONSTRAINT IF EXISTS "
            "instruments_eodhd_exchange_check"
        )
        conn.execute("ALTER TABLE instruments ALTER COLUMN eodhd_type DROP NOT NULL")
        conn.execute(
            "ALTER TABLE instruments ALTER COLUMN eodhd_exchange DROP NOT NULL"
        )
        conn.execute(
            "ALTER TABLE instruments ADD COLUMN IF NOT EXISTS active "
            "BOOLEAN DEFAULT TRUE"
        )
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id IN "
            "('015_instruments_lifecycle_columns', "
            " '016_instruments_eodhd_type_not_null', "
            " '017_instruments_drop_active')"
        )
        conn.commit()
    return migrated_db
