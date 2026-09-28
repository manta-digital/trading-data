"""Integration tests for migrations 057/058 and the CME_EQUITY calendar (221).

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
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg_pool import ConnectionPool
from typer.testing import CliRunner, Result

from manta_trading.cli.app import app
from manta_trading.data.base.session_extension import extend_calendar_sessions
from manta_trading.data.base.trading_calendar import MarketStatus
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations
from manta_trading.market.schema.seed_cme_calendar import (
    CME_EQUITY_CALENDAR_ID,
    CME_EQUITY_EXCEPTIONS,
    CME_EQUITY_HOLIDAYS_SEEDED_THROUGH,
    CME_EQUITY_SEED_START,
)

_CT = ZoneInfo("America/Chicago")
_EXIT_HORIZON_WARN = 4
_FIRST_221_MIGRATION = "057_calendar_holidays_seeded_through"
_CME_SEED_MIGRATION = "058_seed_cme_equity_calendar"
_NYSE_NASDAQ = ("NASDAQ", "NYSE")

runner = CliRunner()


@pytest.fixture
def conn(session_migrated_db: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(session_migrated_db) as connection:
        yield connection


def _ct(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_CT)


def _session(conn: psycopg.Connection[Any], day: date) -> tuple[datetime, datetime]:
    row = conn.execute(
        "SELECT session_open_utc, session_close_utc FROM trading_sessions "
        "WHERE calendar_id = %s AND session_date = %s",
        (CME_EQUITY_CALENDAR_ID, day),
    ).fetchone()
    assert row is not None, f"no CME_EQUITY session dated {day}"
    return row[0], row[1]


def _dump_sessions(
    url: str, calendars: tuple[str, ...] = _NYSE_NASDAQ
) -> list[tuple[Any, ...]]:
    with psycopg.connect(url) as connection:
        return connection.execute(
            "SELECT calendar_id, session_date, session_open_utc, session_close_utc "
            "FROM trading_sessions WHERE calendar_id = ANY(%s) "
            "ORDER BY calendar_id, session_date",
            (list(calendars),),
        ).fetchall()


def invoke_cli(url: str, *args: str) -> Result:
    """Run ``mt`` against ``url`` with a patched Settings (no env URLs read).

    Parse ``result.stdout``: ``result.output`` also carries stderr, where log
    lines (and, in a full-tier run, logging errors from handlers bound to an
    earlier runner's closed stream) land ahead of the JSON.
    """
    settings = MagicMock()
    settings.timescale_db_url = url
    settings.market_db_url = None
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        return runner.invoke(app, list(args), env={"COLUMNS": "200"})


# ---------------------------------------------------------------------------
# Migration 057 / 058 content
# ---------------------------------------------------------------------------


def test_cme_equity_row_matches_d2(conn: psycopg.Connection[Any]) -> None:
    row = conn.execute(
        "SELECT exchange_name, timezone, market_open, market_close, "
        "has_extended_hours, holidays_seeded_through "
        "FROM trading_calendars WHERE calendar_id = %s",
        (CME_EQUITY_CALENDAR_ID,),
    ).fetchone()
    assert row == (
        "CME Globex Equity Index",
        "America/Chicago",
        time(17, 0),
        time(16, 0),
        False,
        CME_EQUITY_HOLIDAYS_SEEDED_THROUGH,
    )


def test_exception_rows_equal_seed_table(conn: psycopg.Connection[Any]) -> None:
    rows = conn.execute(
        "SELECT holiday_date, holiday_name, market_status, early_close_time, "
        "late_open_time FROM trading_holidays WHERE calendar_id = %s "
        "ORDER BY holiday_date",
        (CME_EQUITY_CALENDAR_ID,),
    ).fetchall()
    expected = [
        (
            r["holiday_date"],
            r["holiday_name"],
            r["market_status"].value,
            r["early_close_time"],
            r["late_open_time"],
        )
        for r in CME_EQUITY_EXCEPTIONS
    ]
    assert rows == expected


def test_session_count_is_weekdays_minus_closed(conn: psycopg.Connection[Any]) -> None:
    closed = {
        r["holiday_date"]
        for r in CME_EQUITY_EXCEPTIONS
        if r["market_status"] == MarketStatus.CLOSED
    }
    day, expected = CME_EQUITY_SEED_START, 0
    while day <= CME_EQUITY_HOLIDAYS_SEEDED_THROUGH:
        expected += day.weekday() < 5 and day not in closed
        day += timedelta(days=1)
    count, last = conn.execute(
        "SELECT COUNT(*), MAX(session_date) FROM trading_sessions "
        "WHERE calendar_id = %s",
        (CME_EQUITY_CALENDAR_ID,),
    ).fetchone() or (None, None)
    assert count == expected
    assert last == CME_EQUITY_HOLIDAYS_SEEDED_THROUGH


def test_criterion_3_dates(conn: psycopg.Connection[Any]) -> None:
    labor_day = date(2024, 9, 2)
    assert _session(conn, labor_day) == (
        _ct(date(2024, 9, 1), 17),
        _ct(labor_day, 12),
    )
    assert _session(conn, date(2024, 9, 3))[0] == _ct(labor_day, 17)
    absent = conn.execute(
        "SELECT 1 FROM trading_sessions WHERE calendar_id = %s AND session_date = %s",
        (CME_EQUITY_CALENDAR_ID, date(2024, 12, 25)),
    ).fetchone()
    assert absent is None
    assert _session(conn, date(2024, 12, 26))[0] == _ct(date(2024, 12, 25), 17)


def test_reapplying_is_a_noop(migrated_db: str) -> None:
    """The runner skips applied migrations, and re-running 058 changes nothing."""
    every = (*_NYSE_NASDAQ, CME_EQUITY_CALENDAR_ID)
    before = _dump_sessions(migrated_db, every)
    callable_058 = next(
        m["python_fn"] for m in MINUTE_MIGRATIONS if m["id"] == _CME_SEED_MIGRATION
    )
    with ConnectionPool(migrated_db, min_size=1, max_size=2) as pool:
        assert apply_migrations(pool, MINUTE_MIGRATIONS) == []
        with pool.connection() as connection:
            callable_058(connection)
            connection.commit()
    assert _dump_sessions(migrated_db, every) == before


# ---------------------------------------------------------------------------
# NYSE regression (integration half; the unit half is in
# test_session_population.py::test_nyse_nasdaq_output_matches_pre_221_baseline)
# ---------------------------------------------------------------------------


def test_nyse_sessions_unchanged(ephemeral_db: str) -> None:
    cut = next(
        i for i, m in enumerate(MINUTE_MIGRATIONS) if m["id"] == _FIRST_221_MIGRATION
    )
    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS[:cut])
        through_056 = _dump_sessions(ephemeral_db)
        apply_migrations(pool, MINUTE_MIGRATIONS)
        through_058 = _dump_sessions(ephemeral_db)
        with pool.connection() as connection:
            for calendar_id in _NYSE_NASDAQ:
                first, last = connection.execute(
                    "SELECT MIN(session_date), MAX(session_date) "
                    "FROM trading_sessions WHERE calendar_id = %s",
                    (calendar_id,),
                ).fetchone() or (None, None)
                extend_calendar_sessions(connection, calendar_id, start=first, end=last)
            connection.commit()
    assert through_056, "NYSE/NASDAQ sessions were not populated by 056"
    assert through_058 == through_056
    assert _dump_sessions(ephemeral_db) == through_056


# ---------------------------------------------------------------------------
# CLI against the migrated database
# ---------------------------------------------------------------------------


def test_strict_exits_4_when_bound_is_near(migrated_db: str) -> None:
    bound = date.today() + timedelta(days=30)
    with psycopg.connect(migrated_db) as connection:
        connection.execute(
            "UPDATE trading_calendars SET holidays_seeded_through = %s "
            "WHERE calendar_id = %s",
            (bound, CME_EQUITY_CALENDAR_ID),
        )
        # Throwaway database created by this test's fixture (sql.md).
        connection.execute(
            "DELETE FROM trading_sessions WHERE calendar_id = %s AND session_date > %s",
            (CME_EQUITY_CALENDAR_ID, bound),
        )
        connection.commit()
    result = invoke_cli(
        migrated_db, "data", "extend", "--calendar", CME_EQUITY_CALENDAR_ID, "--strict"
    )
    assert result.exit_code == _EXIT_HORIZON_WARN, result.output
    output = " ".join(result.output.split())
    assert f"holidays seeded through {bound}" in output
    assert "seed the next year's CME_EQUITY schedule" in output


def test_calendars_list_shows_all_three(session_migrated_db: str) -> None:
    result = invoke_cli(session_migrated_db, "data", "calendars", "list", "--json")
    assert result.exit_code == 0, result.output
    bounds = {
        r["calendar_id"]: r["holidays_seeded_through"]
        for r in json.loads(result.stdout)
    }
    assert bounds == {
        CME_EQUITY_CALENDAR_ID: CME_EQUITY_HOLIDAYS_SEEDED_THROUGH.isoformat(),
        "NASDAQ": "2026-12-31",
        "NYSE": "2026-12-31",
    }


def test_calendar_sessions_thanksgiving_week_2024(session_migrated_db: str) -> None:
    result = invoke_cli(
        session_migrated_db,
        "data", "calendars", "sessions",
        "--calendar", CME_EQUITY_CALENDAR_ID,
        "--from", "2024-11-25", "--to", "2024-11-29", "--json",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    rows = {r["session_date"]: r for r in json.loads(result.stdout)}
    assert list(rows) == [
        "2024-11-25", "2024-11-26", "2024-11-27", "2024-11-28", "2024-11-29",
    ]  # fmt: skip
    assert rows["2024-11-28"]["close_local"] == "2024-11-28T12:00:00-06:00"
    assert rows["2024-11-28"]["exception"] == "Thanksgiving Day"
    assert rows["2024-11-29"]["open_local"] == "2024-11-28T17:00:00-06:00"
    assert rows["2024-11-29"]["close_local"] == "2024-11-29T12:15:00-06:00"
