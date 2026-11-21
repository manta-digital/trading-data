"""#83: daily gap detection against real session rows and real daily stamps.

A daily bar is stamped 00:00Z on its session date; the session row carries the
exchange open (14:30Z in winter, 13:30Z in summer). Detection must compare by
session date, so a fully held window reports no gap.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg
import pytest

from manta_trading.data.gaps.compute_missing_ranges import compute_missing_ranges

# SPY's real window from the issue: five sessions, the first spanning New Year.
WINDOW_START = datetime(2021, 12, 29, tzinfo=UTC)
WINDOW_END = datetime(2022, 1, 5, tzinfo=UTC)
SESSION_DATES = [
    date(2021, 12, 29),
    date(2021, 12, 30),
    date(2021, 12, 31),
    date(2022, 1, 3),
    date(2022, 1, 4),
]


def _seed_daily_bars(
    conn: psycopg.Connection[tuple[object, ...]], days: list[date]
) -> None:
    for day in days:
        conn.execute(
            "INSERT INTO daily_ohlcv (time, symbol, open, high, low, close, volume) "
            "VALUES (%s, 'SPY', 1, 1, 1, 1, 1)",
            (datetime(day.year, day.month, day.day, tzinfo=UTC),),
        )


@pytest.fixture
def spy_db(migrated_db: str) -> str:
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange, first_listing_date) "
            "VALUES ('SPY.US', 'SPY', 'equity', 'US', 'NYSE', 'ETF', 'NYSE ARCA', "
            "'2000-01-03')"
        )
    return migrated_db


def _missing(url: str) -> list[tuple[datetime, datetime]]:
    with psycopg.connect(url) as conn:
        ranges = compute_missing_ranges(conn, "SPY", "daily", WINDOW_START, WINDOW_END)
    return [(rng.gap_start_utc, rng.gap_end_utc) for rng in ranges]


def test_fully_held_window_reports_no_gap(spy_db: str) -> None:
    with psycopg.connect(spy_db) as conn:
        _seed_daily_bars(conn, SESSION_DATES)
    assert _missing(spy_db) == []


def test_one_missing_bar_reports_exactly_that_session(spy_db: str) -> None:
    with psycopg.connect(spy_db) as conn:
        _seed_daily_bars(conn, [d for d in SESSION_DATES if d != date(2021, 12, 31)])
    gaps = _missing(spy_db)
    assert len(gaps) == 1
    start, end = gaps[0]
    assert start == end
    assert start.astimezone(UTC).date() == date(2021, 12, 31)


def test_no_bars_reports_one_range_over_every_session(spy_db: str) -> None:
    start, end = _missing(spy_db)[0]
    assert start.astimezone(UTC).date() == SESSION_DATES[0]
    assert end.astimezone(UTC).date() == SESSION_DATES[-1]


def test_window_starting_at_the_open_still_sees_that_sessions_bar(spy_db: str) -> None:
    # A gap row's start is a session open (14:30Z); the bar is stamped 00:00Z
    # the same day, before the window. The first session must still count as held.
    with psycopg.connect(spy_db) as conn:
        _seed_daily_bars(conn, SESSION_DATES)
        first_open = datetime(2021, 12, 29, 14, 30, tzinfo=UTC)
        last_open = datetime(2022, 1, 4, 14, 30, tzinfo=UTC)
        ranges = compute_missing_ranges(conn, "SPY", "daily", first_open, last_open)
    assert ranges == []
