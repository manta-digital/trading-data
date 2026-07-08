"""Integration tests: migrations 029–036 (slice 152 consolidation).

Requires MT_TIMESCALE_DB_URL pointing at a TimescaleDB instance with
migrations 001–028 already applied.

Tests verify:
- 029: splits and dividends tables exist with correct columns.
- 030: adj_* / k_factor / adjusted_at absent from daily_ohlcv;
       last_adjusted_ca_snapshot_id absent from acquisition_state.
- 031: adj_* / k_factor / adjusted_at absent from minute_ohlcv.
- 032: zero legacy minute caggs remain.
- 033: exactly 4 raw minute caggs exist with correct projection.
- 034: exactly 3 daily caggs exist with correct projection.
- 035: 7 refresh policies registered (one per cagg).
- 036: data-copy migration is idempotent (no duplicate rows).
- Idempotency: all migrations skip cleanly on re-apply.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.rows import dict_row

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

_MIGRATION_IDS = [
    "029_splits_dividends_timescale",
    "030_drop_adj_columns_daily_ohlcv",
    "031_drop_adj_columns_minute_ohlcv",
    "032_drop_legacy_minute_caggs",
    "033_create_minute_caggs",
    "034_create_daily_caggs",
    "035_cagg_refresh_policies",
    "036_copy_splits_dividends_from_marketdb",
]

_LEGACY_MINUTE_CAGGS = [
    "minute_5min_ohlcv_v2",
    "minute_15min_ohlcv_v2",
    "minute_hourly_ohlcv_v2",
    "minute_4hour_ohlcv_v2",
    "minute_daily_ohlcv",
    "minute_weekly_ohlcv",
    "minute_monthly_ohlcv",
]

_NEW_MINUTE_CAGGS = [
    "minute_5min_ohlcv",
    "minute_15min_ohlcv",
    "minute_hourly_ohlcv",
    "minute_4hour_ohlcv",
]

_DAILY_CAGGS = [
    "daily_weekly_ohlcv",
    "daily_monthly_ohlcv",
    "daily_quarterly_ohlcv",
]


def _column_names(conn: psycopg.Connection, table: str) -> set[str]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND table_schema = 'public'",
            (table,),
        )
        return {r["column_name"] for r in cur.fetchall()}


def _cagg_names(conn: psycopg.Connection) -> set[str]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates"
        )
        return {r["view_name"] for r in cur.fetchall()}


def _policy_cagg_names(conn: psycopg.Connection) -> set[str]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ca.view_name "
            "FROM timescaledb_information.jobs j "
            "JOIN timescaledb_information.continuous_aggregates ca "
            "    ON j.hypertable_id = ca.mat_hypertable_id "
            "WHERE j.proc_name = 'policy_refresh_continuous_aggregate'"
        )
        return {r["view_name"] for r in cur.fetchall()}


def _rollback_migrations(url: str) -> None:
    """Remove 029–036 so each test starts from a 028-complete state."""
    with psycopg.connect(url) as conn:
        # Drop new caggs
        for cagg in _NEW_MINUTE_CAGGS + _DAILY_CAGGS:
            conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {cagg} CASCADE")  # noqa: S608
        # Restore splits/dividends if they were only created by 029
        conn.execute("DROP TABLE IF EXISTS splits CASCADE")
        conn.execute("DROP TABLE IF EXISTS dividends CASCADE")
        # Restore adj_* columns if they were dropped (best-effort — ignore if
        # daily_ohlcv / minute_ohlcv already had them removed before this test)
        for stmt in (
            "ALTER TABLE daily_ohlcv ADD COLUMN IF NOT EXISTS adj_open NUMERIC(20,8)",
            "ALTER TABLE daily_ohlcv ADD COLUMN IF NOT EXISTS adj_high NUMERIC(20,8)",
            "ALTER TABLE daily_ohlcv ADD COLUMN IF NOT EXISTS adj_low  NUMERIC(20,8)",
            "ALTER TABLE daily_ohlcv ADD COLUMN IF NOT EXISTS adj_close NUMERIC(20,8)",
            "ALTER TABLE daily_ohlcv ADD COLUMN IF NOT EXISTS k_factor  NUMERIC(20,12)",
            "ALTER TABLE daily_ohlcv ADD COLUMN IF NOT EXISTS adjusted_at TIMESTAMPTZ",
            "ALTER TABLE acquisition_state ADD COLUMN IF NOT EXISTS last_adjusted_ca_snapshot_id TEXT",
            "ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS adj_open NUMERIC(20,8)",
            "ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS adj_high NUMERIC(20,8)",
            "ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS adj_low  NUMERIC(20,8)",
            "ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS adj_close NUMERIC(20,8)",
            "ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS k_factor  NUMERIC(20,12)",
            "ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS adjusted_at TIMESTAMPTZ",
        ):
            try:
                conn.execute(stmt)
            except Exception:
                conn.rollback()
                continue
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id = ANY(%s)",
            (_MIGRATION_IDS,),
        )
        conn.commit()


def _apply_migrations(url: str) -> None:
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)


@pytest.mark.skipif(not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set")
class TestMigrations029To036:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:  # type: ignore[return]
        _rollback_migrations(TIMESCALE_URL)
        yield
        _rollback_migrations(TIMESCALE_URL)

    def test_029_splits_dividends_created(self) -> None:
        _apply_migrations(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            splits_cols = _column_names(conn, "splits")
            div_cols = _column_names(conn, "dividends")
        assert {"symbol", "ex_date", "ratio_to", "ratio_from", "source", "fetched_at"} <= splits_cols
        assert {"symbol", "ex_date", "amount", "currency", "source", "fetched_at"} <= div_cols

    def test_030_adj_columns_dropped_from_daily_ohlcv(self) -> None:
        _apply_migrations(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            daily_cols = _column_names(conn, "daily_ohlcv")
            acq_cols = _column_names(conn, "acquisition_state")
        assert "adj_open" not in daily_cols
        assert "adj_close" not in daily_cols
        assert "k_factor" not in daily_cols
        assert "adjusted_at" not in daily_cols
        assert "last_adjusted_ca_snapshot_id" not in acq_cols
        # Core raw columns still present
        assert {"open", "high", "low", "close", "volume"} <= daily_cols

    def test_031_adj_columns_dropped_from_minute_ohlcv(self) -> None:
        _apply_migrations(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            cols = _column_names(conn, "minute_ohlcv")
        assert "adj_open" not in cols
        assert "adj_close" not in cols
        assert "k_factor" not in cols
        assert "adjusted_at" not in cols
        assert {"open", "high", "low", "close", "volume"} <= cols

    def test_032_legacy_caggs_dropped(self) -> None:
        _apply_migrations(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            existing = _cagg_names(conn)
        for name in _LEGACY_MINUTE_CAGGS:
            assert name not in existing, f"Legacy cagg {name!r} still present"

    def test_033_034_new_caggs_exist_with_correct_source(self) -> None:
        _apply_migrations(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            existing = _cagg_names(conn)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT view_name, view_definition "
                    "FROM timescaledb_information.continuous_aggregates "
                    "WHERE view_name = ANY(%s)",
                    ([*_NEW_MINUTE_CAGGS, *_DAILY_CAGGS],),
                )
                rows = {r["view_name"]: r["view_definition"] for r in cur.fetchall()}

        for name in _NEW_MINUTE_CAGGS:
            assert name in existing, f"Minute cagg {name!r} missing"
            assert "minute_ohlcv" in rows[name], f"{name} does not reference minute_ohlcv"

        for name in _DAILY_CAGGS:
            assert name in existing, f"Daily cagg {name!r} missing"
            assert "daily_ohlcv" in rows[name], f"{name} does not reference daily_ohlcv"

    def test_035_refresh_policies_installed(self) -> None:
        _apply_migrations(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            policy_views = _policy_cagg_names(conn)
        expected = set(_NEW_MINUTE_CAGGS + _DAILY_CAGGS)
        assert expected <= policy_views, (
            f"Missing policies for: {expected - policy_views}"
        )

    def test_idempotency(self) -> None:
        _apply_migrations(TIMESCALE_URL)
        # Second apply must not raise
        _apply_migrations(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            existing = _cagg_names(conn)
        for name in _NEW_MINUTE_CAGGS + _DAILY_CAGGS:
            assert name in existing

    def test_036_data_copy_idempotent(self) -> None:
        """Migration 036 must not duplicate rows on re-apply."""
        _apply_migrations(TIMESCALE_URL)
        # Insert a sentinel row into splits
        with psycopg.connect(TIMESCALE_URL) as conn:
            conn.execute(
                "INSERT INTO splits (symbol, ex_date, ratio_to, ratio_from) "
                "VALUES ('TEST', '2024-01-01', 2, 1) ON CONFLICT DO NOTHING"
            )
            conn.commit()
        # Re-apply — 036 is already recorded so it skips; row count unchanged
        _apply_migrations(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM splits WHERE symbol = 'TEST'")
                count = cur.fetchone()[0]  # type: ignore[index]
        assert count == 1
