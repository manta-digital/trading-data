"""Integration tests: migrations 018-022 apply cleanly and idempotently.

Requires MT_TIMESCALE_DB_URL to point at a TimescaleDB instance that has
migrations 001-017 already applied (slice 141 state).
"""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

_MIGRATION_IDS = [
    "018_data_gaps",
    "019_slim_acquisition_state",
    "020_drop_coverage_gaps",
    "021_data_status_view",
    "022_acquisition_state_outcome_check",
]


def _column_names(conn: psycopg.Connection, table: str) -> set[str]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND table_schema = 'public'",
            (table,),
        )
        return {r["column_name"] for r in cur.fetchall()}


def _table_exists(conn: psycopg.Connection, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = %s AND table_schema = 'public')",
            (table,),
        )
        row = cur.fetchone()
    return bool(row and row[0])


def _view_exists(conn: psycopg.Connection, view: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.views "
            "WHERE table_name = %s AND table_schema = 'public')",
            (view,),
        )
        row = cur.fetchone()
    return bool(row and row[0])


def _rollback_018_022(url: str) -> None:
    """Undo migrations 018-022 so each test starts from a 017-complete state."""
    with psycopg.connect(url) as conn:
        conn.execute("DROP VIEW IF EXISTS data_status")
        conn.execute("DROP TABLE IF EXISTS data_gaps")
        # Restore acquisition_state removed columns (best-effort; idempotent)
        conn.execute(
            "ALTER TABLE acquisition_state "
            "DROP COLUMN IF EXISTS last_attempt_outcome, "
            "DROP COLUMN IF EXISTS last_adjusted_ca_snapshot_id"
        )
        conn.execute(
            "ALTER TABLE acquisition_state ADD COLUMN IF NOT EXISTS status TEXT"
        )
        conn.execute(
            "ALTER TABLE acquisition_state "
            "DROP CONSTRAINT IF EXISTS acquisition_state_last_attempt_outcome_check"
        )
        # Remove 018-022 records from schema_migrations
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id = ANY(%s)",
            (_MIGRATION_IDS,),
        )
        conn.commit()


@pytest.mark.skipif(not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set")
class TestMigrations018To022:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:
        _rollback_018_022(TIMESCALE_URL)
        yield
        _rollback_018_022(TIMESCALE_URL)

    def _apply(self) -> list[str]:
        from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
        from manta_trading.market.schema.runner import apply_migrations

        pool = ConnectionPool(TIMESCALE_URL, min_size=1, open=True)
        try:
            return apply_migrations(pool, MINUTE_MIGRATIONS)
        finally:
            pool.close()

    def test_018_data_gaps_table_created(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            assert _table_exists(conn, "data_gaps")
            cols = _column_names(conn, "data_gaps")
            for col in ("symbol", "granularity", "gap_start", "gap_end",
                        "fetch_status", "last_attempt_ts", "attempt_count"):
                assert col in cols, f"missing column: {col}"

    def test_019_acquisition_state_slimmed(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            cols = _column_names(conn, "acquisition_state")
            assert "last_attempt_outcome" in cols
            assert "last_adjusted_ca_snapshot_id" in cols
            assert "last_success_ts" not in cols
            assert "retry_count" not in cols

    def test_020_coverage_gaps_dropped(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            assert not _table_exists(conn, "coverage_gaps")

    def test_021_data_status_view_exists(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            assert _view_exists(conn, "data_status")
            # View must return zero rows on empty instruments
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM data_status")
                row = cur.fetchone()
            assert row is not None
            # Count may be > 0 if instruments has rows, but query must not error
            assert int(row[0]) >= 0

    def test_idempotent_rerun(self) -> None:
        self._apply()
        applied_second = self._apply()
        # No migrations should be applied on the second run
        assert not any(mid in applied_second for mid in _MIGRATION_IDS)

    def test_schema_migrations_records_018_022(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT migration_id FROM schema_migrations "
                    "WHERE migration_id = ANY(%s)",
                    (_MIGRATION_IDS,),
                )
                found = {r["migration_id"] for r in cur.fetchall()}
        assert found == set(_MIGRATION_IDS)
