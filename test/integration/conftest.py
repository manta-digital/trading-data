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

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from tick_support.database import (
    ProvisionedTickDb,
    apply_artifact_or_fail,
    apply_tick_track_as,
    drop_tick_db,
)

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
def kalshi_bare_db(ephemeral_db: str) -> str:
    """Throwaway database with the TimescaleDB extension, nothing else
    (``kalshi_005`` creates a hypertable; production's ``trading`` database
    already has the extension from the minute track)."""
    from kalshi_support.schema import ensure_timescaledb

    return ensure_timescaledb(ephemeral_db)


@pytest.fixture()
def kalshi_db(ephemeral_db: str) -> str:
    """Bare throwaway database → extension → kalshi track applied."""
    from kalshi_support.schema import apply_kalshi_track

    return apply_kalshi_track(ephemeral_db)


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


# ---------------------------------------------------------------------------
# Tick database (slice 923): the provisioning artifact applied for real
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def provisioned_tick_db(test_admin_url: str) -> Iterator[ProvisionedTickDb]:
    """A tick database built by the real artifact, in the documented order.

    Provision → apply the tick track as the migrate role (``mt data init
    --database tick``) → provision again. The second run is what makes the
    ledger SELECT-only: the migrate role's default privileges grant DML on
    every table it creates, the ledger included, and the ledger does not exist
    until the track is applied (measured, slice 923).

    Per-run names, because roles are cluster-wide and the test cluster must
    never gain a real ``tick_app``. Session-scoped: the privilege suite is
    read-only apart from work it rolls back or undoes.
    """
    suffix = uuid.uuid4().hex[:10]
    name = f"mt_test_tp{suffix}"
    tick = ProvisionedTickDb(
        name=name,
        url=urlunparse(urlparse(test_admin_url)._replace(path=f"/{name}")),
        app_role=f"t923_app_{suffix}",
        migrate_role=f"t923_mig_{suffix}",
    )
    try:
        apply_artifact_or_fail(tick, test_admin_url)
        # The artifact grants SET on the migrate role only (it needs it for
        # CREATE DATABASE ... OWNER). The suite also SETs ROLE to the app role;
        # PostgreSQL 16+ CREATEROLE confers ADMIN but not SET (see 913).
        with psycopg.connect(test_admin_url, autocommit=True) as grantor:
            grantor.execute(f'GRANT "{tick.app_role}" TO CURRENT_USER WITH SET TRUE')
        apply_tick_track_as(tick.url, tick.migrate_role)
        apply_artifact_or_fail(tick, test_admin_url)
        yield tick
    finally:
        drop_tick_db(tick, test_admin_url)


@pytest.fixture
def tick_conn(migrated_tick_db: str) -> Iterator[psycopg.Connection[Any]]:
    """An autocommit connection to :func:`migrated_tick_db` (slice 222).

    Autocommit, so a statement a constraint rejects does not abort the next.
    """
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        yield conn
