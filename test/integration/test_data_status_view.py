"""Integration tests for the data_status view (slice 142).

Requires MT_TIMESCALE_DB_URL with slice 141 + 142 schema applied. The
test fixture seeds and tears down its own rows.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.rows import dict_row

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

_TEST_SYMBOL = "ZZZSTAT"
_TEST_CALENDAR = "ZZZSTAT_CAL"
_TEST_PROVIDER = "test_provider_142"


def _ensure_142_applied(url: str) -> None:
    """Apply slice 142 migrations if not already applied."""
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    pool = ConnectionPool(url, min_size=1, open=True)
    try:
        apply_migrations(pool, MINUTE_MIGRATIONS)
    finally:
        pool.close()


def _seed(conn) -> None:
    """Seed instruments + trading_calendar + acquisition_state for the test symbol."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trading_calendars "
            "(calendar_id, exchange_name, timezone, market_open, market_close) "
            "VALUES (%s, 'TEST', 'UTC', '09:30', '16:00') "
            "ON CONFLICT (calendar_id) DO NOTHING",
            (_TEST_CALENDAR,),
        )
        cur.execute(
            "INSERT INTO instruments "
            "(canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange, delisted_at_eodhd) "
            "VALUES (%s, %s, 'equity', 'TEST', %s, 'common_stock', 'TEST', FALSE) "
            "ON CONFLICT (canonical_id) DO NOTHING",
            (f"{_TEST_SYMBOL}.TEST", _TEST_SYMBOL, _TEST_CALENDAR),
        )


def _cleanup(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM data_gaps WHERE symbol = %s", (_TEST_SYMBOL,)
        )
        cur.execute(
            "DELETE FROM acquisition_state WHERE symbol = %s",
            (_TEST_SYMBOL,),
        )
        cur.execute(
            "DELETE FROM instruments WHERE symbol = %s", (_TEST_SYMBOL,)
        )
        cur.execute(
            "DELETE FROM trading_calendars WHERE calendar_id = %s",
            (_TEST_CALENDAR,),
        )


def _query_health(conn, granularity: str) -> str | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT health FROM data_status "
            "WHERE symbol = %s AND granularity = %s",
            (_TEST_SYMBOL, granularity),
        )
        row = cur.fetchone()
    return row["health"] if row else None


@pytest.mark.skipif(not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set")
class TestDataStatusHealthRules:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:
        _ensure_142_applied(TIMESCALE_URL)
        with psycopg.connect(TIMESCALE_URL) as conn:
            _cleanup(conn)
            _seed(conn)
            conn.commit()
        yield
        with psycopg.connect(TIMESCALE_URL) as conn:
            _cleanup(conn)
            conn.commit()

    def test_no_acquisition_state_yields_stale(self) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            health_daily = _query_health(conn, "daily")
            health_minute = _query_health(conn, "minute")
        assert health_daily == "STALE"
        assert health_minute == "STALE"

    def test_recent_attempt_no_gaps_yields_ok(self) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO acquisition_state "
                    "(symbol, granularity, provider, last_attempt_ts, "
                    "last_attempt_outcome) "
                    "VALUES (%s, 'daily', %s, NOW(), 'success')",
                    (_TEST_SYMBOL, _TEST_PROVIDER),
                )
            conn.commit()
            health = _query_health(conn, "daily")
        assert health == "OK"

    def test_unknown_gap_yields_gaps(self) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO acquisition_state "
                    "(symbol, granularity, provider, last_attempt_ts, "
                    "last_attempt_outcome) "
                    "VALUES (%s, 'daily', %s, NOW(), 'success')",
                    (_TEST_SYMBOL, _TEST_PROVIDER),
                )
                cur.execute(
                    "INSERT INTO data_gaps "
                    "(symbol, granularity, gap_start, gap_end, fetch_status, "
                    "attempt_count) "
                    "VALUES (%s, 'daily', "
                    "NOW() - INTERVAL '1 day', NOW(), 'UNKNOWN', 0)",
                    (_TEST_SYMBOL,),
                )
            conn.commit()
            health = _query_health(conn, "daily")
        assert health == "GAPS"

    def test_retry_exhausted_yields_failed(self) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO acquisition_state "
                    "(symbol, granularity, provider, last_attempt_ts, "
                    "last_attempt_outcome) "
                    "VALUES (%s, 'daily', %s, NOW(), 'success')",
                    (_TEST_SYMBOL, _TEST_PROVIDER),
                )
                cur.execute(
                    "INSERT INTO data_gaps "
                    "(symbol, granularity, gap_start, gap_end, fetch_status, "
                    "attempt_count) "
                    "VALUES (%s, 'daily', "
                    "NOW() - INTERVAL '1 day', NOW(), 'RETRY_EXHAUSTED', 5)",
                    (_TEST_SYMBOL,),
                )
            conn.commit()
            health = _query_health(conn, "daily")
        assert health == "FAILED"

    def test_old_attempt_yields_stale(self) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO acquisition_state "
                    "(symbol, granularity, provider, last_attempt_ts, "
                    "last_attempt_outcome) "
                    "VALUES (%s, 'daily', %s, "
                    "NOW() - INTERVAL '7 days', 'success')",
                    (_TEST_SYMBOL, _TEST_PROVIDER),
                )
            conn.commit()
            health = _query_health(conn, "daily")
        assert health == "STALE"
