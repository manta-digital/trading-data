"""Integration tests: migration 043 (minute_ohlcv chunk interval, slice 166).

Requires MT_TIMESCALE_DB_URL pointing at a TimescaleDB instance with the
minute migration chain applied (the runner is idempotent, so these tests
apply pending migrations themselves).

Verifies:
- After the chain runs, minute_ohlcv's time-dimension interval equals
  MINUTE_OHLCV_CHUNK_INTERVAL (guards against reverting to 4 hours).
- Migration 043 is idempotent (re-apply is a no-op).
"""

from __future__ import annotations

import os
from datetime import timedelta

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.constants import MINUTE_OHLCV_CHUNK_INTERVAL
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

_MIGRATION_ID = "043_minute_chunk_interval_7d"


@pytest.mark.skipif(not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set")
class TestMigration043ChunkInterval:
    def _apply(self) -> list[str]:
        from manta_trading.market.schema.runner import apply_migrations

        with ConnectionPool(TIMESCALE_URL, min_size=1, max_size=2, open=True) as pool:
            return apply_migrations(pool, MINUTE_MIGRATIONS)

    def _dimension_interval(self) -> timedelta:
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT time_interval FROM timescaledb_information.dimensions "
                    "WHERE hypertable_name = 'minute_ohlcv'"
                )
                row = cur.fetchone()
        assert row is not None, "minute_ohlcv has no time dimension"
        return row[0]

    def test_dimension_interval_equals_constant(self) -> None:
        self._apply()
        assert self._dimension_interval() == MINUTE_OHLCV_CHUNK_INTERVAL

    def test_043_recorded_as_applied(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM schema_migrations WHERE migration_id = %s",
                    (_MIGRATION_ID,),
                )
                assert cur.fetchone() is not None

    def test_043_idempotent(self) -> None:
        """Second apply must be a no-op (no exception, nothing newly applied)."""
        self._apply()
        newly = self._apply()
        assert _MIGRATION_ID not in newly
