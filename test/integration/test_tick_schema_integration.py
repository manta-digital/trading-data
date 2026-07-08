"""Integration tests for migration 760: tick_events hypertable.

All tests skip when MT_TICK_DB_URL is not set.

Run with:
    MT_TICK_DB_URL=postgresql://... uv run pytest \
        test/integration/test_tick_schema_integration.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from psycopg.errors import CheckViolation

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

TICK_URL = os.environ.get("MT_TICK_DB_URL", "")
skip_no_db = pytest.mark.skipif(
    not TICK_URL,
    reason="MT_TICK_DB_URL not set — skipping tick schema integration tests",
)

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "database" / "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_migration(filename: str) -> str:
    return (MIGRATIONS_DIR / filename).read_text()


def _table_exists(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'tick_events'"
        )
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tick_db():
    """Establish a psycopg connection to the tick DB; close on teardown."""
    conn = psycopg.connect(TICK_URL, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture()
def clean_tick_db(tick_db):
    """Run rollback before and after each test for isolation."""
    rollback_sql = _read_migration("760_rollback_tick_events_hypertable.sql")
    tick_db.execute(rollback_sql)
    yield tick_db
    tick_db.execute(rollback_sql)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_no_db
class TestForwardMigration:
    """Forward migration creates table and hypertable correctly."""

    def test_forward_migration_applies_cleanly(self, clean_tick_db):
        forward_sql = _read_migration("760_create_tick_events_hypertable.sql")
        clean_tick_db.execute(forward_sql)
        assert _table_exists(clean_tick_db)

    def test_forward_migration_is_idempotent(self, clean_tick_db):
        forward_sql = _read_migration("760_create_tick_events_hypertable.sql")
        clean_tick_db.execute(forward_sql)
        # Second application must not raise
        clean_tick_db.execute(forward_sql)
        assert _table_exists(clean_tick_db)


@skip_no_db
class TestHypertableConfiguration:
    """Hypertable dimensions are configured as specified."""

    @pytest.fixture(autouse=True)
    def _apply_migration(self, clean_tick_db):
        forward_sql = _read_migration("760_create_tick_events_hypertable.sql")
        clean_tick_db.execute(forward_sql)
        self.conn = clean_tick_db

    def test_hypertable_exists(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'tick_events'"
            )
            assert cur.fetchone() is not None

    def test_chunk_interval_is_one_hour(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT time_interval FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = 'tick_events' AND column_name = 'timestamp'"
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0].total_seconds() == 3600

    def test_space_dimension_instrument_id_four_partitions(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT num_partitions FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = 'tick_events' AND column_name = 'instrument_id'"
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == 4

    def test_compression_enabled(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT compression_enabled FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'tick_events'"
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] is True


@skip_no_db
class TestIndexes:
    """Expected indexes are present."""

    @pytest.fixture(autouse=True)
    def _apply_migration(self, clean_tick_db):
        forward_sql = _read_migration("760_create_tick_events_hypertable.sql")
        clean_tick_db.execute(forward_sql)
        self.conn = clean_tick_db

    def _index_exists(self, index_name: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_indexes WHERE tablename = 'tick_events' "
                "AND indexname = %s",
                (index_name,),
            )
            return cur.fetchone() is not None

    def test_natural_key_index_exists(self):
        assert self._index_exists("idx_tick_events_natural_key")

    def test_instrument_time_index_exists(self):
        assert self._index_exists("idx_tick_events_instrument_time")

    def test_type_index_exists(self):
        assert self._index_exists("idx_tick_events_type")


@skip_no_db
class TestCheckConstraints:
    """CHECK constraints enforce valid values."""

    TRADE_ROW = {
        "instrument_id": 1,
        "timestamp": "2026-01-02 10:00:00+00",
        "sequence_number": 1,
        "source": "test",
        "event_type": "trade",
        "price": 100.0,
        "size": 10.0,
    }
    QUOTE_ROW = {
        "instrument_id": 1,
        "timestamp": "2026-01-02 10:00:01+00",
        "sequence_number": 2,
        "source": "test",
        "event_type": "quote",
        "bid_price": 99.99,
        "ask_price": 100.01,
    }

    @pytest.fixture(autouse=True)
    def _apply_migration(self, clean_tick_db):
        forward_sql = _read_migration("760_create_tick_events_hypertable.sql")
        clean_tick_db.execute(forward_sql)
        self.conn = clean_tick_db

    def _insert(self, row: dict) -> None:
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f"%({k})s" for k in row.keys())
        self.conn.execute(
            f"INSERT INTO tick_events ({cols}) VALUES ({placeholders})",
            row,
        )

    def test_insert_trade_succeeds(self):
        self._insert(self.TRADE_ROW)

    def test_insert_quote_succeeds(self):
        self._insert(self.QUOTE_ROW)

    def test_invalid_event_type_raises(self):
        bad = {**self.TRADE_ROW, "sequence_number": 99, "event_type": "invalid"}
        with pytest.raises(CheckViolation):
            self._insert(bad)

    def test_instrument_id_zero_raises(self):
        bad = {**self.TRADE_ROW, "sequence_number": 98, "instrument_id": 0}
        with pytest.raises(CheckViolation):
            self._insert(bad)

    def test_instrument_id_negative_raises(self):
        bad = {**self.TRADE_ROW, "sequence_number": 97, "instrument_id": -1}
        with pytest.raises(CheckViolation):
            self._insert(bad)


@skip_no_db
class TestIdempotentInsert:
    """ON CONFLICT DO UPDATE allows idempotent ingestion."""

    @pytest.fixture(autouse=True)
    def _apply_migration(self, clean_tick_db):
        forward_sql = _read_migration("760_create_tick_events_hypertable.sql")
        clean_tick_db.execute(forward_sql)
        self.conn = clean_tick_db

    def test_on_conflict_updates_price(self):
        self.conn.execute(
            "INSERT INTO tick_events "
            "(instrument_id, timestamp, sequence_number, source, event_type, price, size) "
            "VALUES (1, '2026-01-02 10:00:00+00', 1, 'test', 'trade', 100.0, 10.0)"
        )
        self.conn.execute(
            "INSERT INTO tick_events "
            "(instrument_id, timestamp, sequence_number, source, event_type, price, size) "
            "VALUES (1, '2026-01-02 10:00:00+00', 1, 'test', 'trade', 200.0, 10.0) "
            "ON CONFLICT (instrument_id, timestamp, sequence_number, source) "
            "DO UPDATE SET price = EXCLUDED.price"
        )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT price FROM tick_events "
                "WHERE instrument_id = 1 AND sequence_number = 1 AND source = 'test'"
            )
            row = cur.fetchone()
        assert row is not None
        assert float(row[0]) == 200.0


@skip_no_db
class TestRollbackMigration:
    """Rollback removes the table cleanly."""

    def test_rollback_removes_table(self, clean_tick_db):
        forward_sql = _read_migration("760_create_tick_events_hypertable.sql")
        clean_tick_db.execute(forward_sql)
        assert _table_exists(clean_tick_db)

        rollback_sql = _read_migration("760_rollback_tick_events_hypertable.sql")
        clean_tick_db.execute(rollback_sql)
        assert not _table_exists(clean_tick_db)
