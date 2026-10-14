"""Integration tests for migration 059 and the CME_METALS calendar (232).

Every test runs on a throwaway database created by the conftest fixtures.
Read-only checks share one session-scoped database; tests that write get their
own. The CLI is driven with ``Settings`` patched to the throwaway URL, so no
production variable is read.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg_pool import ConnectionPool
from test_migration_057_058_cme_calendar import invoke_cli

from manta_trading.data.base.trading_calendar import MarketStatus
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations
from manta_trading.market.schema.seed_cme_calendar import CME_EQUITY_CALENDAR_ID
from manta_trading.market.schema.seed_cme_metals_calendar import (
    CME_METALS_CALENDAR_ID,
    CME_METALS_EXCEPTIONS,
    CME_METALS_HOLIDAYS_SEEDED_THROUGH,
    CME_METALS_SEED_START,
)

_CT = ZoneInfo("America/Chicago")
_METALS_SEED_MIGRATION = "059_seed_cme_metals_calendar"
_OTHERS = ("NASDAQ", "NYSE", CME_EQUITY_CALENDAR_ID)


@pytest.fixture
def conn(session_migrated_db: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(session_migrated_db) as connection:
        yield connection


def _ct(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_CT)


def _session(
    conn: psycopg.Connection[Any], day: date
) -> tuple[datetime, datetime] | None:
    row = conn.execute(
        "SELECT session_open_utc, session_close_utc FROM trading_sessions "
        "WHERE calendar_id = %s AND session_date = %s",
        (CME_METALS_CALENDAR_ID, day),
    ).fetchone()
    return None if row is None else (row[0], row[1])


def _dump(url: str, calendars: tuple[str, ...]) -> dict[str, list[tuple[Any, ...]]]:
    """Calendar rows, exceptions and sessions of ``calendars``, in a stable order."""
    queries = {
        "calendars": "SELECT * FROM trading_calendars WHERE calendar_id = ANY(%s) "
        "ORDER BY calendar_id",
        "holidays": "SELECT * FROM trading_holidays WHERE calendar_id = ANY(%s) "
        "ORDER BY calendar_id, holiday_date",
        "sessions": "SELECT * FROM trading_sessions WHERE calendar_id = ANY(%s) "
        "ORDER BY calendar_id, session_date",
    }
    with psycopg.connect(url) as connection:
        return {
            name: connection.execute(sql, (list(calendars),)).fetchall()
            for name, sql in queries.items()
        }


def _callable_059() -> Any:
    return next(
        m["python_fn"] for m in MINUTE_MIGRATIONS if m["id"] == _METALS_SEED_MIGRATION
    )


# ---------------------------------------------------------------------------
# Migration 059 content
# ---------------------------------------------------------------------------


def test_cme_metals_row_matches_td1(conn: psycopg.Connection[Any]) -> None:
    row = conn.execute(
        "SELECT exchange_name, timezone, market_open, market_close, "
        "extended_open, extended_close, has_extended_hours, holidays_seeded_through "
        "FROM trading_calendars WHERE calendar_id = %s",
        (CME_METALS_CALENDAR_ID,),
    ).fetchone()
    assert row == (
        "CME Globex Metals",
        "America/Chicago",
        time(17, 0),
        time(16, 0),
        None,
        None,
        False,
        CME_METALS_HOLIDAYS_SEEDED_THROUGH,
    )


def test_exception_rows_equal_seed_table(conn: psycopg.Connection[Any]) -> None:
    rows = conn.execute(
        "SELECT holiday_date, holiday_name, market_status, early_close_time, "
        "late_open_time FROM trading_holidays WHERE calendar_id = %s "
        "ORDER BY holiday_date",
        (CME_METALS_CALENDAR_ID,),
    ).fetchall()
    expected = [
        (
            r["holiday_date"],
            r["holiday_name"],
            r["market_status"].value,
            r["early_close_time"],
            r["late_open_time"],
        )
        for r in CME_METALS_EXCEPTIONS
    ]
    assert rows == expected


def test_sessions_span_seed_start_to_bound(conn: psycopg.Connection[Any]) -> None:
    closed = {
        r["holiday_date"]
        for r in CME_METALS_EXCEPTIONS
        if r["market_status"] == MarketStatus.CLOSED
    }
    day, expected = CME_METALS_SEED_START, 0
    while day <= CME_METALS_HOLIDAYS_SEEDED_THROUGH:
        expected += day.weekday() < 5 and day not in closed
        day += timedelta(days=1)
    count, first, last = conn.execute(
        "SELECT COUNT(*), MIN(session_date), MAX(session_date) FROM trading_sessions "
        "WHERE calendar_id = %s",
        (CME_METALS_CALENDAR_ID,),
    ).fetchone() or (None, None, None)
    assert count == expected
    assert first == date(2020, 1, 2)  # 2020-01-01 is closed
    assert last == CME_METALS_HOLIDAYS_SEEDED_THROUGH


def test_purchased_range_exception_sessions(conn: psycopg.Connection[Any]) -> None:
    """TD7's sessions: metals times, not equity's, and no Christmas/New Year."""
    thanksgiving, black_friday = date(2024, 11, 28), date(2024, 11, 29)
    assert _session(conn, thanksgiving) == (
        _ct(date(2024, 11, 27), 17),
        _ct(thanksgiving, 13, 30),
    )
    assert _session(conn, black_friday) == (
        _ct(thanksgiving, 17),
        _ct(black_friday, 13, 45),
    )
    assert _session(conn, date(2024, 12, 24)) == (
        _ct(date(2024, 12, 23), 17),
        _ct(date(2024, 12, 24), 12, 45),
    )
    assert _session(conn, date(2024, 12, 25)) is None
    assert _session(conn, date(2025, 1, 1)) is None
    assert _session(conn, date(2024, 12, 26)) == (
        _ct(date(2024, 12, 25), 17),
        _ct(date(2024, 12, 26), 16),
    )


def test_other_calendars_unchanged_by_059(ephemeral_db: str) -> None:
    cut = next(
        i for i, m in enumerate(MINUTE_MIGRATIONS) if m["id"] == _METALS_SEED_MIGRATION
    )
    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS[:cut])
        before = _dump(ephemeral_db, _OTHERS)
        apply_migrations(pool, MINUTE_MIGRATIONS)
    assert before["sessions"], "other calendars were not populated before 059"
    assert _dump(ephemeral_db, _OTHERS) == before


def test_reapplying_is_a_noop(migrated_db: str) -> None:
    """The runner skips applied migrations, and re-running 059 changes nothing."""
    every = (*_OTHERS, CME_METALS_CALENDAR_ID)
    before = _dump(migrated_db, every)
    with ConnectionPool(migrated_db, min_size=1, max_size=2) as pool:
        assert apply_migrations(pool, MINUTE_MIGRATIONS) == []
        with pool.connection() as connection:
            for _ in range(2):
                _callable_059()(connection)
                connection.commit()
    assert _dump(migrated_db, every) == before


# ---------------------------------------------------------------------------
# CLI against the migrated database (criteria 1 and 3)
# ---------------------------------------------------------------------------


def test_calendars_list_shows_cme_metals(session_migrated_db: str) -> None:
    result = invoke_cli(session_migrated_db, "data", "calendars", "list", "--json")
    assert result.exit_code == 0, result.output
    bounds = {
        r["calendar_id"]: r["holidays_seeded_through"]
        for r in json.loads(result.stdout)
    }
    bound = CME_METALS_HOLIDAYS_SEEDED_THROUGH.isoformat()
    assert bounds[CME_METALS_CALENDAR_ID] == bound


def test_calendar_sessions_thanksgiving_week_2024(session_migrated_db: str) -> None:
    result = invoke_cli(
        session_migrated_db,
        "data", "calendars", "sessions",
        "--calendar", CME_METALS_CALENDAR_ID,
        "--from", "2024-11-27", "--to", "2024-12-02", "--json",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    rows = {r["session_date"]: r for r in json.loads(result.stdout)}
    assert list(rows) == ["2024-11-27", "2024-11-28", "2024-11-29", "2024-12-02"]
    assert rows["2024-11-28"]["close_local"] == "2024-11-28T13:30:00-06:00"
    assert rows["2024-11-28"]["exception"] == "Thanksgiving Day"
    assert rows["2024-11-29"]["open_local"] == "2024-11-28T17:00:00-06:00"
    assert rows["2024-11-29"]["close_local"] == "2024-11-29T13:45:00-06:00"
