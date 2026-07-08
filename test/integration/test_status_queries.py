"""Integration tests for status_queries DB fetch helpers (slice 147 T7).

Requires MT_TIMESCALE_DB_URL with slice 142+ schema applied.
Seeds and tears down its own rows to avoid polluting the test DB.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from manta_trading.cli.rendering.status_table import HealthStatus
from manta_trading.data.maintenance.status_queries import (
    fetch_all_health_counts,
    fetch_status_rows,
    fetch_symbol_gaps,
)

_TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not _TIMESCALE_URL,
    reason="MT_TIMESCALE_DB_URL not set",
)

_TEST_SYMBOL = "ZZZSTATQ_147"
_TEST_CALENDAR = "ZZZSTATQ_CAL"
_TEST_PROVIDER = "test_provider_147"

_NOW = datetime.now(timezone.utc)


def _seed(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trading_calendars "
            "(calendar_id, exchange_name, timezone, market_open, market_close) "
            "VALUES (%s, 'TEST147', 'UTC', '09:30', '16:00') "
            "ON CONFLICT (calendar_id) DO NOTHING",
            (_TEST_CALENDAR,),
        )
        cur.execute(
            "INSERT INTO instruments "
            "(canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange, delisted_at_eodhd) "
            "VALUES (%s, %s, 'equity', 'TEST147', %s, 'common_stock', 'TEST147', FALSE) "
            "ON CONFLICT (canonical_id) DO NOTHING",
            (f"{_TEST_SYMBOL}.TEST147", _TEST_SYMBOL, _TEST_CALENDAR),
        )
    conn.commit()


def _seed_gap(conn: psycopg.Connection, fetch_status: str = "RETRY_EXHAUSTED") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO data_gaps
                (symbol, granularity, gap_start, gap_end, fetch_status, attempt_count)
            VALUES (%s, 'daily',
                    '2024-01-02 14:30:00+00', '2024-01-02 21:00:00+00',
                    %s, 5)
            ON CONFLICT DO NOTHING
            """,
            (_TEST_SYMBOL, fetch_status),
        )
    conn.commit()


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM data_gaps WHERE symbol = %s", (_TEST_SYMBOL,))
        cur.execute(
            "DELETE FROM acquisition_state WHERE symbol = %s", (_TEST_SYMBOL,)
        )
        cur.execute("DELETE FROM instruments WHERE symbol = %s", (_TEST_SYMBOL,))
        cur.execute(
            "DELETE FROM trading_calendars WHERE calendar_id = %s", (_TEST_CALENDAR,)
        )
    conn.commit()


@pytest.fixture(autouse=True)
def db_fixture():
    with psycopg.connect(_TIMESCALE_URL) as conn:
        _cleanup(conn)
        _seed(conn)
    yield
    with psycopg.connect(_TIMESCALE_URL) as conn:
        _cleanup(conn)


# ---------------------------------------------------------------------------
# fetch_status_rows
# ---------------------------------------------------------------------------


def test_fetch_status_rows_no_filter() -> None:
    with psycopg.connect(_TIMESCALE_URL) as conn:
        rows = fetch_status_rows(conn, symbol=_TEST_SYMBOL, health_filter=None)
    # Should have rows for daily and/or minute (view cross-joins instruments with granularities)
    assert isinstance(rows, list)
    symbols = {r.symbol for r in rows}
    assert _TEST_SYMBOL in symbols


def test_fetch_status_rows_health_filter() -> None:
    """health_filter=["FAILED"] returns only FAILED rows (or empty)."""
    _seed_gap(psycopg.connect(_TIMESCALE_URL), "RETRY_EXHAUSTED")
    with psycopg.connect(_TIMESCALE_URL) as conn:
        rows = fetch_status_rows(
            conn,
            symbol=_TEST_SYMBOL,
            health_filter=[HealthStatus.FAILED],
        )
    for r in rows:
        assert r.health == HealthStatus.FAILED


def test_fetch_status_rows_symbol_filter() -> None:
    with psycopg.connect(_TIMESCALE_URL) as conn:
        rows = fetch_status_rows(conn, symbol=_TEST_SYMBOL, health_filter=None)
    for r in rows:
        assert r.symbol == _TEST_SYMBOL


def test_fetch_status_rows_granularity_filter() -> None:
    with psycopg.connect(_TIMESCALE_URL) as conn:
        rows = fetch_status_rows(
            conn,
            symbol=_TEST_SYMBOL,
            health_filter=None,
            granularity="daily",
        )
    for r in rows:
        assert r.granularity == "daily"


# ---------------------------------------------------------------------------
# fetch_symbol_gaps
# ---------------------------------------------------------------------------


def test_fetch_symbol_gaps_ordered() -> None:
    """Gaps returned in ascending gap_start order."""
    with psycopg.connect(_TIMESCALE_URL) as conn:
        # Seed two gaps with different starts
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_gaps
                    (symbol, granularity, gap_start, gap_end, fetch_status, attempt_count)
                VALUES
                    (%s, 'daily', '2024-03-01 14:30:00+00', '2024-03-01 21:00:00+00', 'UNKNOWN', 1),
                    (%s, 'daily', '2024-01-01 14:30:00+00', '2024-01-01 21:00:00+00', 'UNKNOWN', 1)
                ON CONFLICT DO NOTHING
                """,
                (_TEST_SYMBOL, _TEST_SYMBOL),
            )
        conn.commit()

        gaps = fetch_symbol_gaps(conn, _TEST_SYMBOL)

    assert len(gaps) >= 2
    for i in range(len(gaps) - 1):
        assert gaps[i].gap_start <= gaps[i + 1].gap_start


def test_fetch_symbol_gaps_empty() -> None:
    """Unknown symbol returns empty list without exception."""
    with psycopg.connect(_TIMESCALE_URL) as conn:
        gaps = fetch_symbol_gaps(conn, "DOES_NOT_EXIST_XYZ")
    assert gaps == []


# ---------------------------------------------------------------------------
# fetch_all_health_counts
# ---------------------------------------------------------------------------


def test_fetch_all_health_counts_sums() -> None:
    """Total count across all health values equals total data_status row count."""
    with psycopg.connect(_TIMESCALE_URL) as conn:
        counts = fetch_all_health_counts(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM data_status")
            row = cur.fetchone()
    total_view = int(row[0]) if row else 0
    total_counts = sum(counts.values())
    assert total_counts == total_view
