"""Integration tests: migrations 023 and 024 apply cleanly and idempotently.

Requires MT_TIMESCALE_DB_URL pointing at a TimescaleDB instance that already
has migrations 001-022 applied (slice 142 state).

Tests verify:
- After 023: daily_ohlcv is a hypertable with chunk_time_interval = 7 days
  and the expected column list.
- After 024: EXPLAIN SELECT * FROM data_status references daily_ohlcv in the
  query plan (confirming the with-daily view branch is active).
"""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

_MIGRATION_IDS = [
    "023_daily_ohlcv",
    "024_data_status_view_refresh",
]


def _column_names(conn: psycopg.Connection, table: str) -> set[str]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND table_schema = 'public'",
            (table,),
        )
        return {r["column_name"] for r in cur.fetchall()}


def _rollback_023_024(url: str) -> None:
    """Undo migrations 023-024 so each test starts from a 022-complete state."""
    with psycopg.connect(url) as conn:
        conn.execute("DROP TABLE IF EXISTS daily_ohlcv CASCADE")
        # Re-install the without-daily view variant to match post-022 state.
        from manta_trading.market.schema.migrations.minute import (
            _DATA_STATUS_VIEW_WITHOUT_DAILY,
        )
        conn.execute(_DATA_STATUS_VIEW_WITHOUT_DAILY)
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id = ANY(%s)",
            (_MIGRATION_IDS,),
        )
        conn.commit()


@pytest.mark.skipif(not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set")
class TestMigrations023To024:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:  # type: ignore[return]
        _rollback_023_024(TIMESCALE_URL)
        yield
        _rollback_023_024(TIMESCALE_URL)

    def _apply(self) -> list[str]:
        from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
        from manta_trading.market.schema.runner import apply_migrations

        pool = ConnectionPool(TIMESCALE_URL, min_size=1, open=True)
        try:
            return apply_migrations(pool, MINUTE_MIGRATIONS)
        finally:
            pool.close()

    def test_023_daily_ohlcv_table_created(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            cols = _column_names(conn, "daily_ohlcv")
            for col in (
                "time", "symbol", "open", "high", "low", "close", "volume",
                "adj_open", "adj_high", "adj_low", "adj_close",
                "k_factor", "adjusted_at", "created_at",
            ):
                assert col in cols, f"missing column: {col}"

    def test_023_daily_ohlcv_is_hypertable(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hypertable_name "
                    "FROM timescaledb_information.hypertables "
                    "WHERE hypertable_name = 'daily_ohlcv'"
                )
                row = cur.fetchone()
        assert row is not None, "daily_ohlcv is not a hypertable"

    def test_023_daily_ohlcv_chunk_interval_7_days(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT time_interval "
                    "FROM timescaledb_information.dimensions "
                    "WHERE hypertable_name = 'daily_ohlcv'"
                )
                row = cur.fetchone()
        assert row is not None, "no dimension found for daily_ohlcv"
        import datetime
        assert row[0] == datetime.timedelta(days=7), (
            f"expected 7 days chunk interval, got {row[0]}"
        )

    def test_024_data_status_view_references_daily_ohlcv(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("EXPLAIN SELECT * FROM data_status")
                plan_rows = cur.fetchall()
        plan_text = " ".join(str(r) for r in plan_rows)
        assert "daily_ohlcv" in plan_text, (
            "EXPLAIN plan for data_status does not reference daily_ohlcv; "
            "migration 024 may not have switched to the with-daily branch"
        )

    def test_idempotent_rerun(self) -> None:
        self._apply()
        applied_second = self._apply()
        assert not any(mid in applied_second for mid in _MIGRATION_IDS)

    def test_schema_migrations_records_023_024(self) -> None:
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
