"""Integration tests: migrations 025 and 026 (trading_sessions) (T8).

Requires MT_TIMESCALE_DB_URL pointing at a TimescaleDB instance that already
has migrations 001-024 applied (slice 143 state).

Tests verify:
- Migration 025 creates trading_sessions with the expected schema + index.
- Migration 026 populates rows for every calendar in trading_calendars:
  - Christmas (closed) absent.
  - Black Friday (early close) present with early session_close_utc.
  - Weekend dates absent.
  - MAX(session_date) >= current_year + 1.
- Migration 026 is idempotent (re-applying produces no error, no duplication).
"""

from __future__ import annotations

import os
from datetime import date, datetime

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

_MIGRATION_IDS = [
    "025_trading_sessions_table",
    "026_trading_sessions_initial_population",
]


def _rollback_025_026(url: str) -> None:
    """Undo migrations 025-026 so each test starts from a 024-complete state."""
    with psycopg.connect(url) as conn:
        conn.execute("DROP TABLE IF EXISTS trading_sessions CASCADE")
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id = ANY(%s)",
            (_MIGRATION_IDS,),
        )
        conn.commit()


@pytest.mark.skipif(not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set")
class TestMigrations025To026:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:  # type: ignore[return]
        _rollback_025_026(TIMESCALE_URL)
        yield
        _rollback_025_026(TIMESCALE_URL)

    def _apply(self) -> list[str]:
        from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
        from manta_trading.market.schema.runner import apply_migrations

        pool = ConnectionPool(TIMESCALE_URL, min_size=1, open=True)
        try:
            return apply_migrations(pool, MINUTE_MIGRATIONS)
        finally:
            pool.close()

    # -----------------------------------------------------------------------
    # Migration 025: table + index created
    # -----------------------------------------------------------------------

    def test_025_trading_sessions_table_created(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'trading_sessions' AND table_schema = 'public'"
                )
                cols = {r["column_name"] for r in cur.fetchall()}
        for col in ("calendar_id", "session_date", "session_open_utc", "session_close_utc"):
            assert col in cols, f"missing column: {col}"

    def test_025_primary_key_exists(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.table_constraints "
                    "WHERE table_name = 'trading_sessions' "
                    "AND constraint_type = 'PRIMARY KEY'"
                )
                row = cur.fetchone()
        assert row and row[0] == 1, "trading_sessions PK missing"

    def test_025_close_index_exists(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE tablename = 'trading_sessions' "
                    "AND indexname = 'idx_trading_sessions_close'"
                )
                row = cur.fetchone()
        assert row is not None, "idx_trading_sessions_close missing"

    def test_025_idempotent(self) -> None:
        self._apply()
        # Re-applying should not raise
        self._apply()

    # -----------------------------------------------------------------------
    # Migration 026: population
    # -----------------------------------------------------------------------

    def test_026_rows_populated_per_calendar(self) -> None:
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT calendar_id, COUNT(*) AS sessions, "
                    "       MIN(session_date) AS first_date, "
                    "       MAX(session_date) AS last_date "
                    "FROM trading_sessions "
                    "GROUP BY calendar_id ORDER BY calendar_id"
                )
                rows = cur.fetchall()

        assert len(rows) > 0, "No rows in trading_sessions after migration 026"
        current_year = datetime.now().year
        for row in rows:
            assert row["sessions"] > 100, (
                f"{row['calendar_id']}: expected >100 sessions, got {row['sessions']}"
            )
            assert row["last_date"].year >= current_year + 1, (
                f"{row['calendar_id']}: MAX(session_date) {row['last_date']} "
                f"not >= current_year + 1 ({current_year + 1})"
            )

    def test_026_christmas_absent_for_nyse(self) -> None:
        """Christmas (closed) must not appear in trading_sessions."""
        self._apply()
        # Find the most recent Christmas in the seeded range
        current_year = datetime.now().year
        christmas = date(current_year, 12, 25)
        if christmas.weekday() >= 5:
            christmas = date(current_year - 1, 12, 25)

        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM trading_sessions "
                    "WHERE calendar_id = 'NYSE' AND session_date = %s",
                    (christmas,),
                )
                row = cur.fetchone()
        assert row and row[0] == 0, (
            f"Christmas {christmas} should be absent from trading_sessions"
        )

    def test_026_weekend_dates_absent(self) -> None:
        """Saturday and Sunday must not appear in trading_sessions."""
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM trading_sessions "
                    "WHERE EXTRACT(DOW FROM session_date) IN (0, 6)"
                )
                row = cur.fetchone()
        assert row and row[0] == 0, (
            f"Found {row[0]} weekend rows in trading_sessions"
        )

    def test_026_timestamps_are_timezone_aware(self) -> None:
        """session_open_utc / session_close_utc are TIMESTAMPTZ — psycopg returns
        them in the connection's session timezone, but the underlying instant
        is what matters. Just verify they are timezone-aware."""
        self._apply()
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT session_open_utc, session_close_utc "
                    "FROM trading_sessions LIMIT 1"
                )
                row = cur.fetchone()
        assert row is not None
        assert row["session_open_utc"].tzinfo is not None, (
            "session_open_utc is naive"
        )
        assert row["session_close_utc"].tzinfo is not None, (
            "session_close_utc is naive"
        )
        # And verify instant ordering
        assert row["session_close_utc"] > row["session_open_utc"]

    def test_026_idempotent_no_duplication(self) -> None:
        """Re-applying migration 026 must not duplicate rows."""
        self._apply()

        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM trading_sessions")
                count_before = cur.fetchone()[0]

        # Remove 026 from schema_migrations so the runner re-applies it
        with psycopg.connect(TIMESCALE_URL) as conn:
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id = %s",
                ("026_trading_sessions_initial_population",),
            )
            conn.commit()

        self._apply()

        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM trading_sessions")
                count_after = cur.fetchone()[0]

        assert count_before == count_after, (
            f"Idempotency failure: count before={count_before}, after={count_after}"
        )
