"""Integration tests for migration 057 and ``extend_calendar_sessions`` (221 D6).

Every test runs on a throwaway database the fixture created and migrated
(``migrated_db``); nothing here reads a production URL.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.data.base.session_eras import is_dated
from manta_trading.data.base.session_extension import extend_calendar_sessions
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_TEST_CALENDAR = "ZZZ221_EXT"
_BOUND_DAYS_OUT = 30


@pytest.fixture
def conn(migrated_db: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(migrated_db) as connection:
        yield connection


@pytest.fixture
def bounded_calendar(conn: psycopg.Connection) -> date:
    """A CME-hours test calendar whose holidays are seeded 30 days out."""
    bound = date.today() + timedelta(days=_BOUND_DAYS_OUT)
    conn.execute(
        "INSERT INTO trading_calendars "
        "(calendar_id, exchange_name, timezone, market_open, market_close, "
        " holidays_seeded_through) "
        "VALUES (%s, 'TEST221', 'America/Chicago', '17:00', '16:00', %s)",
        (_TEST_CALENDAR, bound),
    )
    conn.commit()
    return bound


def test_057_column_is_not_null(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'trading_calendars' "
        "AND column_name = 'holidays_seeded_through'"
    ).fetchone()
    assert row == ("NO",)


def test_057_nyse_nasdaq_seeded_through_2026(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "SELECT calendar_id, holidays_seeded_through FROM trading_calendars "
        "WHERE calendar_id IN ('NYSE', 'NASDAQ') ORDER BY calendar_id"
    ).fetchall()
    assert rows == [("NASDAQ", date(2026, 12, 31)), ("NYSE", date(2026, 12, 31))]


def test_extension_clamps_to_holiday_bound(
    conn: psycopg.Connection, bounded_calendar: date
) -> None:
    result = extend_calendar_sessions(
        conn,
        _TEST_CALENDAR,
        start=date.today(),
        end=date.today() + timedelta(days=365),
    )
    assert result.clamped_by_holiday_bound is True
    assert result.holidays_seeded_through == bounded_calendar
    assert result.rows_upserted > 0
    assert result.horizon_after is not None
    assert result.horizon_after <= bounded_calendar


def test_extension_inside_bound_is_not_clamped(
    conn: psycopg.Connection, bounded_calendar: date
) -> None:
    result = extend_calendar_sessions(
        conn, _TEST_CALENDAR, start=date.today(), end=bounded_calendar
    )
    assert result.clamped_by_holiday_bound is False


def test_unknown_calendar_raises(conn: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="ZZZ221_MISSING"):
        extend_calendar_sessions(
            conn, "ZZZ221_MISSING", start=date.today(), end=date.today()
        )


def test_second_call_from_horizon_is_noop(
    conn: psycopg.Connection, bounded_calendar: date
) -> None:
    end = date.today() + timedelta(days=365)
    first = extend_calendar_sessions(conn, _TEST_CALENDAR, start=date.today(), end=end)
    assert first.horizon_after is not None
    second = extend_calendar_sessions(
        conn, _TEST_CALENDAR, start=first.horizon_after + timedelta(days=1), end=end
    )
    assert second.rows_upserted == 0
    assert second.horizon_after == first.horizon_after


def test_no_calendar_is_dated_before_the_column_exists(ephemeral_db: str) -> None:
    """301 D3: 058/059 on a fresh database run before ``hours_dated`` exists."""
    cut = next(
        i
        for i, m in enumerate(MINUTE_MIGRATIONS)
        if m["id"] == "059_seed_cme_metals_calendar"
    )
    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS[: cut + 1])
    with psycopg.connect(ephemeral_db) as connection:
        for calendar_id in ("CME_EQUITY", "CME_METALS", "NYSE"):
            assert is_dated(connection, calendar_id) is False
