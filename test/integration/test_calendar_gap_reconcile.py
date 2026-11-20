"""Gap reconciliation over the changed calendar dates (slice 174 task 5.2).

Runs on a throwaway database with the full chain (migration 068 applied), so
2021-12-31 is a session and 2025-01-09 is not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest

from manta_trading.data.gaps.calendar_reconcile import reconcile_calendar_gaps
from manta_trading.market.schema.nyse_session_changes import (
    EXPECTED_SESSION_CHANGES,
)

DATES = EXPECTED_SESSION_CHANGES.reconciled_dates()
OPEN_1231 = datetime(2021, 12, 31, 14, 30, tzinfo=UTC)
CLOSE_1231 = datetime(2021, 12, 31, 21, 0, tzinfo=UTC)
OPEN_0109 = datetime(2025, 1, 9, 14, 30, tzinfo=UTC)
CLOSE_0109 = datetime(2025, 1, 9, 21, 0, tzinfo=UTC)
SPAN = (
    datetime(2025, 1, 8, 14, 30, tzinfo=UTC),
    datetime(2025, 1, 10, 21, 0, tzinfo=UTC),
)
LAST_ATTEMPT = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)
UNRELATED = (
    datetime(2024, 6, 3, 13, 30, tzinfo=UTC),
    datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
)


def _instrument(conn: psycopg.Connection[Any], symbol: str, first: date) -> None:
    conn.execute(
        "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
        "trading_calendar_id, eodhd_type, eodhd_exchange, first_data_date) "
        "VALUES (%s, %s, 'equity', 'US', 'NYSE', 'Common Stock', 'NYSE', %s)",
        (f"{symbol}.US", symbol, first),
    )


def _state(conn: psycopg.Connection[Any], symbol: str, outcome: str | None) -> None:
    for granularity in ("minute", "daily"):
        conn.execute(
            "INSERT INTO acquisition_state (symbol, granularity, provider, "
            "last_attempt_outcome, last_attempt_ts) VALUES (%s, %s, 'eodhd', %s, %s)",
            (symbol, granularity, outcome, LAST_ATTEMPT if outcome else None),
        )


def _gap(
    conn: psycopg.Connection[Any],
    symbol: str,
    granularity: str,
    start: datetime,
    end: datetime,
    status: str = "UNKNOWN",
    attempts: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO data_gaps (symbol, granularity, gap_start, gap_end, "
        "fetch_status, attempt_count) VALUES (%s, %s, %s, %s, %s, %s)",
        (symbol, granularity, start, end, status, attempts),
    )


def _bars_on_1231(conn: psycopg.Connection[Any], symbol: str) -> None:
    conn.execute(
        "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, volume) "
        "VALUES (%s, %s, 1, 1, 1, 1, 10)",
        (datetime(2021, 12, 31, 15, 0, tzinfo=UTC), symbol),
    )
    conn.execute(
        "INSERT INTO daily_ohlcv (time, symbol, open, high, low, close, volume) "
        "VALUES (%s, %s, 1, 1, 1, 1, 10)",
        (datetime(2021, 12, 31, tzinfo=UTC), symbol),
    )


@pytest.fixture
def seeded(migrated_db: str) -> str:
    with psycopg.connect(migrated_db) as conn:
        for symbol in ("HOLE", "MISS", "HAS", "NEVER", "JUDGED"):
            _instrument(conn, symbol, date(2020, 1, 2))
        _instrument(conn, "LATE", date(2023, 3, 1))
        for symbol, outcome in (
            ("HOLE", "success"), ("MISS", "partial"), ("HAS", "success"),
            ("LATE", "success"), ("NEVER", None), ("JUDGED", "success"),
        ):  # fmt: skip
            _state(conn, symbol, outcome)
        for symbol in ("HOLE", "HAS", "JUDGED"):
            _bars_on_1231(conn, symbol)
        _gap(conn, "HOLE", "minute", OPEN_0109, CLOSE_0109)
        _gap(conn, "HOLE", "daily", OPEN_0109, OPEN_0109)
        _gap(conn, "JUDGED", "minute", OPEN_0109, CLOSE_0109, status="PROVIDER_HOLE")
        _gap(conn, "HOLE", "minute", *SPAN, status="PROVIDER_HOLE", attempts=3)
        _gap(conn, "HOLE", "minute", *UNRELATED, attempts=2)
    return migrated_db


def _gaps(conn: psycopg.Connection[Any], symbol: str) -> set[tuple[Any, ...]]:
    rows = conn.execute(
        "SELECT granularity, gap_start, gap_end, fetch_status, attempt_count "
        "FROM data_gaps WHERE symbol = %s",
        (symbol,),
    ).fetchall()
    return set(rows)


def _outcomes(conn: psycopg.Connection[Any]) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT symbol, granularity, last_attempt_outcome, last_attempt_ts "
        "FROM acquisition_state ORDER BY 1, 2"
    ).fetchall()


def test_reconciles_each_day_only(seeded: str) -> None:
    with psycopg.connect(seeded, autocommit=True) as conn:
        outcomes_before = _outcomes(conn)
        report = reconcile_calendar_gaps(conn, DATES)

        # The closed day loses its own rows; the multi-day terminal row and a
        # row on an unchanged date keep their status and attempts.
        assert _gaps(conn, "HOLE") == {
            ("minute", *SPAN, "PROVIDER_HOLE", 3),
            ("minute", *UNRELATED, "UNKNOWN", 2),
        }
        # The opened day is seeded only where bars are missing.
        assert {(g, s, e, st) for g, s, e, st, _ in _gaps(conn, "MISS")} == {
            ("minute", OPEN_1231, CLOSE_1231, "UNKNOWN"),
            ("daily", OPEN_1231, OPEN_1231, "UNKNOWN"),
        }
        # A terminal row inside the closed day goes too.
        assert _gaps(conn, "JUDGED") == set()
        assert _gaps(conn, "HAS") == set()
        assert _gaps(conn, "LATE") == set()
        assert _gaps(conn, "NEVER") == set()
        assert report.skipped_never_attempted == {
            (date(2021, 12, 31), "minute"): 1,
            (date(2021, 12, 31), "daily"): 1,
        }
        assert _outcomes(conn) == outcomes_before

        changed = {
            (c.day, c.granularity, c.symbol, c.before, c.after) for c in report.changed
        }
        assert changed == {
            (date(2025, 1, 9), "minute", "HOLE", 2, 1),
            (date(2025, 1, 9), "daily", "HOLE", 1, 0),
            (date(2025, 1, 9), "minute", "JUDGED", 1, 0),
            (date(2021, 12, 31), "minute", "MISS", 0, 1),
            (date(2021, 12, 31), "daily", "MISS", 0, 1),
        }

        again = reconcile_calendar_gaps(conn, DATES)
        assert again.is_noop()
