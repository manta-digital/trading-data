"""#83: shrinking existing daily gap rows, on a throwaway database.

The rows are built the way the bug wrote them: one range from the first to the
last session of a window, whatever bars were held.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest

from manta_trading.data.gaps.daily_gap_reconcile import reconcile_daily_gaps

# NYSE sessions Mon 2024-01-02 .. Fri 2024-01-12 (2024-01-01 is a holiday).
SESSIONS = [date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10, 11, 12)]


def _open(day: date) -> datetime:
    """The 14:30Z open every January NYSE session row carries."""
    return datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)


def _stamp(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _add_symbol(conn: psycopg.Connection[Any], symbol: str, held: list[date]) -> None:
    conn.execute(
        "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
        "trading_calendar_id, eodhd_type, eodhd_exchange, first_listing_date) "
        "VALUES (%s, %s, 'equity', 'US', 'NYSE', 'Common Stock', 'NYSE', "
        "'2000-01-03')",
        (f"{symbol}.US", symbol),
    )
    for day in held:
        conn.execute(
            "INSERT INTO daily_ohlcv (time, symbol, open, high, low, close, volume) "
            "VALUES (%s, %s, 1, 1, 1, 1, 1)",
            (_stamp(day), symbol),
        )


def _add_gap(
    conn: psycopg.Connection[Any],
    symbol: str,
    start: date,
    end: date,
    status: str,
    attempts: int,
) -> None:
    conn.execute(
        "INSERT INTO data_gaps (symbol, granularity, gap_start, gap_end, "
        "fetch_status, last_attempt_ts, attempt_count) "
        "VALUES (%s, 'daily', %s, %s, %s, %s, %s)",
        (symbol, _open(start), _open(end), status, _stamp(date(2026, 11, 1)), attempts),
    )


def _rows(conn: psycopg.Connection[Any], symbol: str) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT gap_start::date, gap_end::date, fetch_status, attempt_count "
        "FROM data_gaps WHERE symbol = %s AND granularity = 'daily' "
        "ORDER BY gap_start",
        (symbol,),
    ).fetchall()


@pytest.fixture
def conn(migrated_db: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(migrated_db, autocommit=True) as connection:
        # Held bars: FULL has every session; PART has all but Jan 4 and Jan 5, 9;
        # NONE has none. Each carries the bug's one-range row over the window.
        _add_symbol(connection, "FULL", SESSIONS)
        _add_symbol(connection, "PART", [d for d in SESSIONS if d.day not in (4, 5, 9)])
        _add_symbol(connection, "NONE", [])
        _add_gap(connection, "FULL", SESSIONS[0], SESSIONS[-1], "RETRY_EXHAUSTED", 5)
        _add_gap(connection, "PART", SESSIONS[0], SESSIONS[-1], "PROVIDER_HOLE", 3)
        _add_gap(connection, "NONE", SESSIONS[0], SESSIONS[-1], "UNKNOWN", 1)
        yield connection


def test_apply_deletes_full_splits_partial_and_keeps_none(
    conn: psycopg.Connection[Any],
) -> None:
    report = reconcile_daily_gaps(conn, apply=True)

    assert _rows(conn, "FULL") == []
    # Jan 4 and 5 are adjacent sessions, Jan 9 is separate: two ranges, each
    # keeping the terminal status and the attempt count.
    assert _rows(conn, "PART") == [
        (date(2024, 1, 4), date(2024, 1, 5), "PROVIDER_HOLE", 3),
        (date(2024, 1, 9), date(2024, 1, 9), "PROVIDER_HOLE", 3),
    ]
    assert _rows(conn, "NONE") == [(SESSIONS[0], SESSIONS[-1], "UNKNOWN", 1)]
    assert (report.deleted, report.split, report.unchanged) == (1, 1, 1)
    assert (report.rows_before, report.rows_after) == (3, 3)


def test_dry_run_counts_the_change_and_writes_nothing(
    conn: psycopg.Connection[Any],
) -> None:
    before = {s: _rows(conn, s) for s in ("FULL", "PART", "NONE")}

    report = reconcile_daily_gaps(conn, apply=False)

    assert {s: _rows(conn, s) for s in before} == before
    assert (report.deleted, report.split, report.unchanged) == (1, 1, 1)


def test_second_apply_changes_nothing(conn: psycopg.Connection[Any]) -> None:
    reconcile_daily_gaps(conn, apply=True)
    after_first = {s: _rows(conn, s) for s in ("FULL", "PART", "NONE")}

    report = reconcile_daily_gaps(conn, apply=True)

    assert {s: _rows(conn, s) for s in after_first} == after_first
    assert report.deleted == report.shrunk == report.split == 0


def test_symbol_without_a_lifecycle_anchor_is_left_as_found(
    conn: psycopg.Connection[Any],
) -> None:
    # No instrument row at all: the row cannot be judged, so it must survive.
    _add_gap(conn, "GONE", SESSIONS[0], SESSIONS[-1], "UNKNOWN", 0)

    report = reconcile_daily_gaps(conn, apply=True)

    assert _rows(conn, "GONE") == [(SESSIONS[0], SESSIONS[-1], "UNKNOWN", 0)]
    assert report.skipped_no_lifecycle == 1
