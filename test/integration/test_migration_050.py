"""Integration tests: migration 050 (daily_ohlcv chunk interval, slice 170).

Runs against a throwaway database the fixture creates and drops itself
(``ephemeral_db``, from ``test/conftest.py``), so nothing here can reach a
configured production database — the 2026-08-04 incident's rule. Requires
``MT_TIMESCALE_TEST_URL`` pointing at an *admin* connection on a
TimescaleDB-equipped instance.

Verifies:
- After the chain runs, daily_ohlcv's time-dimension interval equals
  DAILY_OHLCV_CHUNK_INTERVAL (guards against reverting to 7 days).
- Migration 050 is recorded and is idempotent (re-apply is a no-op).
- 70 days nests the pre-170 7-day interval exactly, which is what lets
  ``mt data rechunk --table daily`` collapse whole chunks per window.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest

from manta_trading.constants import DAILY_OHLCV_CHUNK_INTERVAL, DAILY_OHLCV_TABLE
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_MIGRATION_ID = "050_daily_chunk_interval_70d"


def _apply(url: str) -> list[str]:
    db = TimescaleMinuteDataDB(conninfo=url)
    try:
        return db.apply_schema_migrations()
    finally:
        db.close()


def _dimension_interval(url: str) -> timedelta:
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT time_interval FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = %s",
                (DAILY_OHLCV_TABLE,),
            )
            row = cur.fetchone()
    assert row is not None, f"{DAILY_OHLCV_TABLE} has no time dimension"
    return row[0]


class TestMigration050DailyChunkInterval:
    def test_dimension_interval_equals_constant(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        assert _dimension_interval(ephemeral_db) == DAILY_OHLCV_CHUNK_INTERVAL

    def test_050_recorded_as_applied(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        with psycopg.connect(ephemeral_db) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM schema_migrations WHERE migration_id = %s",
                    (_MIGRATION_ID,),
                )
                assert cur.fetchone() is not None

    def test_050_idempotent(self, ephemeral_db: str) -> None:
        """Second apply must be a no-op, and must not disturb the interval."""
        first = _apply(ephemeral_db)
        assert _MIGRATION_ID in first, "050 should apply on a fresh database"

        newly = _apply(ephemeral_db)
        assert _MIGRATION_ID not in newly
        assert _dimension_interval(ephemeral_db) == DAILY_OHLCV_CHUNK_INTERVAL

    def test_interval_nests_the_pre_170_interval(self) -> None:
        """70 = 10 x 7. Without exact nesting a 7-day chunk could straddle a
        70-day boundary, and the rechunk driver's per-window rewrite would no
        longer see whole chunks (slice 166's grid-alignment caveat)."""
        assert DAILY_OHLCV_CHUNK_INTERVAL % timedelta(days=7) == timedelta(0)


@pytest.mark.parametrize("migration_id", [_MIGRATION_ID])
def test_migration_is_present_in_the_chain(migration_id: str) -> None:
    """Cheap guard that runs without a database: the chain still carries 050."""
    assert any(m["id"] == migration_id for m in MINUTE_MIGRATIONS)
