"""Shared fixtures for integration tests.

The ``ephemeral_db`` fixture lives in ``test/conftest.py`` (shared with the
load tier); only integration-specific fixtures belong here.
"""

from __future__ import annotations

import os

import psycopg
import pytest

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")


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
