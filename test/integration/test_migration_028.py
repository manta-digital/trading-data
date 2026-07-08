"""Integration tests: migration 028 (data_status view rewrite) (T12).

Requires MT_TIMESCALE_DB_URL pointing at a TimescaleDB instance that already
has migrations 001-026 applied (slice 144 migrations 025+026 state).

Tests verify:
- After 028: data_status.target_end_ts is non-NULL for symbols with a
  matching trading_calendar_id that has sessions in trading_sessions.
- Symbols whose trading_calendar_id has NO rows in trading_sessions still
  appear in the view (LEFT JOIN preserved), with target_end_ts = NULL.
- Query latency is sub-second over the full universe.
- Migration 028 is idempotent.
"""

from __future__ import annotations

import os
import time

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

_MIGRATION_ID = "028_data_status_view_target_end_ts"


def _rollback_028(url: str) -> None:
    """Restore the pre-028 view and remove the migration record.

    The pre-rendered view SQL constants in minute.py are escaped for EXECUTE
    (`''daily''`), so we wrap them in a DO block to unescape via EXECUTE.
    """
    with psycopg.connect(url) as conn:
        from manta_trading.market.schema.migrations.minute import (
            _DATA_STATUS_VIEW_WITH_DAILY,
            _DATA_STATUS_VIEW_WITHOUT_DAILY,
        )
        view_sql = (
            _DATA_STATUS_VIEW_WITH_DAILY if _has_daily_ohlcv(conn)
            else _DATA_STATUS_VIEW_WITHOUT_DAILY
        )
        conn.execute(f"DO $$ BEGIN EXECUTE '{view_sql}'; END $$;")
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id = %s",
            (_MIGRATION_ID,),
        )
        conn.commit()


def _has_daily_ohlcv(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.daily_ohlcv')")
        row = cur.fetchone()
    return row is not None and row[0] is not None


@pytest.mark.skipif(not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set")
class TestMigration028:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:  # type: ignore[return]
        _rollback_028(TIMESCALE_URL)
        yield
        _rollback_028(TIMESCALE_URL)

    def _apply(self) -> list[str]:
        from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
        from manta_trading.market.schema.runner import apply_migrations

        pool = ConnectionPool(TIMESCALE_URL, min_size=1, open=True)
        try:
            return apply_migrations(pool, MINUTE_MIGRATIONS)
        finally:
            pool.close()

    def test_028_applied_in_newly_applied_list(self) -> None:
        applied = self._apply()
        assert _MIGRATION_ID in applied

    def test_028_target_end_ts_from_trading_sessions(self) -> None:
        """Symbols with a known NYSE/NASDAQ calendar get non-NULL target_end_ts.

        This test only asserts non-NULL if trading_sessions is populated and
        at least one session has already closed (i.e. NOT in the future).
        If all sessions are future-only, target_end_ts is legitimately NULL.
        """
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT COUNT(*) AS cnt "
                    "FROM trading_sessions ts "
                    "WHERE ts.session_close_utc < NOW() - INTERVAL '30 minutes'"
                )
                closed_row = cur.fetchone()

            if not closed_row or closed_row["cnt"] == 0:
                pytest.skip("No closed sessions in trading_sessions — cannot assert non-NULL")

            # Pick any instrument tied to a calendar that has closed sessions
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT ds.symbol, ds.target_end_ts "
                    "FROM data_status ds "
                    "JOIN instruments i ON i.symbol = ds.symbol "
                    "WHERE i.trading_calendar_id IN ("
                    "    SELECT DISTINCT calendar_id FROM trading_sessions "
                    "    WHERE session_close_utc < NOW() - INTERVAL '30 minutes'"
                    ") "
                    "LIMIT 5"
                )
                rows = cur.fetchall()

        assert len(rows) > 0, "No instruments with a calendar that has closed sessions"
        for row in rows:
            assert row["target_end_ts"] is not None, (
                f"target_end_ts is NULL for {row['symbol']} despite populated trading_sessions"
            )

    def test_028_left_join_preserved_for_unknown_calendar(self) -> None:
        """Symbols with unknown trading_calendar_id appear in view with NULL target_end_ts."""
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # Find a symbol whose calendar has NO rows in trading_sessions
                cur.execute(
                    "SELECT ds.symbol, ds.target_end_ts "
                    "FROM data_status ds "
                    "JOIN instruments i ON i.symbol = ds.symbol "
                    "WHERE i.trading_calendar_id IS NULL "
                    "   OR i.trading_calendar_id NOT IN ("
                    "       SELECT DISTINCT calendar_id FROM trading_sessions"
                    ") "
                    "LIMIT 1"
                )
                row = cur.fetchone()

        if row is None:
            pytest.skip("All instruments have calendars in trading_sessions; skipping LEFT JOIN test")
        assert row["target_end_ts"] is None, (
            f"Expected NULL target_end_ts for unknown calendar, got {row['target_end_ts']}"
        )

    def test_028_query_latency_sub_second(self) -> None:
        """Full data_status scan must complete in under 1 second."""
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            start = time.monotonic()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM data_status")
                cur.fetchone()
            elapsed = time.monotonic() - start

        assert elapsed < 1.0, (
            f"data_status full scan took {elapsed:.3f}s — exceeded 1s NFR"
        )

    def test_028_idempotent(self) -> None:
        """Re-applying migration 028 must not raise."""
        self._apply()
        # Remove migration record so runner re-applies
        with psycopg.connect(TIMESCALE_URL) as conn:
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id = %s",
                (_MIGRATION_ID,),
            )
            conn.commit()
        # Should succeed without exception
        self._apply()
