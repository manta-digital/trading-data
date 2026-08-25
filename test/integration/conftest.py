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

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

if TYPE_CHECKING:
    from manta_trading.data.kalshi.repository import CatalogRepository

# ``migrated_db`` (ephemeral DB + full migration chain) lives in
# ``test/conftest.py`` so the unit tier's DB-backed tests share it.


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


# ---------------------------------------------------------------------------
# Kalshi (slice 262): a throwaway database with the kalshi track applied
# ---------------------------------------------------------------------------


@pytest.fixture()
def kalshi_db(ephemeral_db: str) -> str:
    """Bare throwaway database → kalshi track applied."""
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations import TRACKS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool[Any](ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, TRACKS["kalshi"])
    return ephemeral_db


@pytest.fixture()
async def kalshi_conn(kalshi_db: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """One async connection in autocommit mode — the sync's own model, where
    every write is inside an explicit ``transaction()`` block."""
    async with await psycopg.AsyncConnection.connect(
        kalshi_db, autocommit=True
    ) as conn:
        yield conn


@pytest.fixture()
def kalshi_repo(kalshi_conn: psycopg.AsyncConnection[Any]) -> CatalogRepository:
    from manta_trading.data.kalshi.repository import CatalogRepository

    return CatalogRepository(kalshi_conn)
