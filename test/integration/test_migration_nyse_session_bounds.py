"""Integration tests for migration 068, NYSE/NASDAQ session bounds (slice 174 4.3).

Every test runs on a throwaway database the conftest fixtures created.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from manta_trading.data.base.session_extension import extend_calendar_sessions
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.nyse_calendar_history import (
    NYSE_HOLIDAYS_SEEDED_THROUGH,
    US_EQUITY_CALENDAR_IDS,
)
from manta_trading.market.schema.nyse_session_changes import (
    EXPECTED_SESSION_CHANGES,
)
from manta_trading.market.schema.nyse_session_plan import plan_nyse_session_changes
from manta_trading.market.schema.runner import apply_migrations

MIGRATION_ID = "068_nyse_session_bounds"
FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "calendar"
    / "nyse_nasdaq_sessions_pre_221.json"
)


def _chain_before() -> list[dict[str, Any]]:
    ids = [m["id"] for m in MINUTE_MIGRATIONS]
    return MINUTE_MIGRATIONS[: ids.index(MIGRATION_ID)]


def _migrate(url: str, migrations: list[dict[str, Any]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, migrations)


def _load_production_shape(url: str) -> None:
    """NYSE/NASDAQ sessions through 2028 from the old rule data, as production."""
    data = json.loads(FIXTURE.read_text())
    with psycopg.connect(url) as conn:
        conn.execute(
            "DELETE FROM trading_sessions WHERE calendar_id = ANY(%s)",
            (list(US_EQUITY_CALENDAR_IDS),),
        )
        with conn.cursor() as cur:
            for calendar_id in US_EQUITY_CALENDAR_IDS:
                cur.executemany(
                    "INSERT INTO trading_sessions (calendar_id, session_date, "
                    "session_open_utc, session_close_utc) VALUES (%s, %s, %s, %s)",
                    [(calendar_id, *row) for row in data[calendar_id]],
                )


def _session(conn: psycopg.Connection[Any], calendar_id: str, day: date) -> Any:
    return conn.execute(
        "SELECT session_open_utc, session_close_utc, extended_open_utc, "
        "extended_close_utc FROM trading_sessions "
        "WHERE calendar_id = %s AND session_date = %s",
        (calendar_id, day),
    ).fetchone()


def test_plan_migration_and_result(ephemeral_db: str) -> None:
    _migrate(ephemeral_db, _chain_before())
    _load_production_shape(ephemeral_db)
    with psycopg.connect(ephemeral_db) as conn:
        plan = plan_nyse_session_changes(conn)
    for calendar_id in US_EQUITY_CALENDAR_IDS:
        assert plan[calendar_id] == EXPECTED_SESSION_CHANGES, calendar_id

    _migrate(ephemeral_db, MINUTE_MIGRATIONS)

    with psycopg.connect(ephemeral_db) as conn:
        for calendar_id in US_EQUITY_CALENDAR_IDS:
            assert _session(conn, calendar_id, date(2021, 12, 31)) is not None
            for day in EXPECTED_SESSION_CHANGES.closed:
                assert _session(conn, calendar_id, day) is None, day
            # Early close: 13:00 EST close, 17:00 EST extended close.
            early = _session(conn, calendar_id, date(2025, 11, 28))
            assert early[1] == datetime(2025, 11, 28, 18, tzinfo=UTC)
            assert early[3] == datetime(2025, 11, 28, 22, tzinfo=UTC)
            # A wrongly shortened July 2 is a full day again (16:00, 20:00 EDT).
            july2 = _session(conn, calendar_id, date(2026, 7, 2))
            assert july2[1] == datetime(2026, 7, 2, 20, tzinfo=UTC)
            assert july2[3] == datetime(2026, 7, 3, 0, tzinfo=UTC)
        missing = conn.execute(
            "SELECT count(*) FROM trading_sessions WHERE calendar_id = ANY(%s) "
            "AND (extended_open_utc IS NULL OR extended_close_utc IS NULL)",
            (list(US_EQUITY_CALENDAR_IDS),),
        ).fetchone()
        assert missing == (0,)
        bounds = conn.execute(
            "SELECT DISTINCT holidays_seeded_through FROM trading_calendars "
            "WHERE calendar_id = ANY(%s)",
            (list(US_EQUITY_CALENDAR_IDS),),
        ).fetchall()
        assert bounds == [(NYSE_HOLIDAYS_SEEDED_THROUGH,)]
        extended_close = conn.execute(
            "SELECT DISTINCT extended_close_time FROM trading_holidays "
            "WHERE calendar_id = 'NYSE' AND market_status = 'early_close'"
        ).fetchall()
        assert extended_close == [(time(17, 0),)]
        after = plan_nyse_session_changes(conn)
    assert all(changes.is_empty() for changes in after.values()), after


def test_extension_after_migration_writes_extended_bounds(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "DELETE FROM trading_sessions WHERE calendar_id = 'NYSE' "
            "AND session_date >= '2028-12-01'"
        )
        extend_calendar_sessions(
            conn, "NYSE", start=date(2028, 12, 1), end=date(2028, 12, 31)
        )
        rows = conn.execute(
            "SELECT count(*), count(extended_open_utc), count(extended_close_utc) "
            "FROM trading_sessions WHERE calendar_id = 'NYSE' "
            "AND session_date >= '2028-12-01'"
        ).fetchone()
        conn.rollback()
    assert rows is not None and rows[0] > 0
    assert rows[0] == rows[1] == rows[2]
