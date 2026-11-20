"""The slice 174 calendar cutover's database steps (task 6.2), on a throwaway DB.

Host steps (timers, install, serve) are not exercised; ``migrate`` is replaced
by applying the chain to the throwaway database.
"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.nyse_calendar_history import US_EQUITY_CALENDAR_IDS
from manta_trading.market.schema.runner import apply_migrations

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "test" / "fixtures" / "calendar" / "nyse_nasdaq_sessions_pre_221.json"
MIGRATION_ID = "068_nyse_session_bounds"


@pytest.fixture
def cutover() -> ModuleType:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("cutover_174_calendar")


def _migrate(url: str, migrations: list[dict[str, Any]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, migrations)


def _before_068() -> list[dict[str, Any]]:
    ids = [m["id"] for m in MINUTE_MIGRATIONS]
    return MINUTE_MIGRATIONS[: ids.index(MIGRATION_ID)]


@pytest.fixture
def production_shape(ephemeral_db: str) -> str:
    """At 067, NYSE/NASDAQ sessions through 2028 from the old rule data, and one
    symbol with a gap row on 2025-01-09 and none of its 2021-12-31 bars."""
    _migrate(ephemeral_db, _before_068())
    data = json.loads(FIXTURE.read_text())
    with psycopg.connect(ephemeral_db) as conn:
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
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange, first_data_date) "
            "VALUES ('ABC.US', 'ABC', 'equity', 'US', 'NYSE', 'Common Stock', "
            "'NYSE', '2020-01-02')"
        )
        for granularity in ("minute", "daily"):
            conn.execute(
                "INSERT INTO acquisition_state (symbol, granularity, provider, "
                "last_attempt_outcome) VALUES ('ABC', %s, 'eodhd', 'success')",
                (granularity,),
            )
        conn.execute(
            "INSERT INTO data_gaps (symbol, granularity, gap_start, gap_end, "
            "fetch_status) VALUES ('ABC', 'minute', %s, %s, 'UNKNOWN')",
            (
                datetime(2025, 1, 9, 14, 30, tzinfo=UTC),
                datetime(2025, 1, 9, 21, tzinfo=UTC),
            ),
        )
    return ephemeral_db


def _apply(url: str) -> Any:
    return lambda: _migrate(url, MINUTE_MIGRATIONS)


def _changed(report: Any) -> set[tuple[Any, ...]]:
    return {(c.day, c.granularity, c.symbol, c.before, c.after) for c in report.changed}


def test_first_run_applies_and_reconciles_then_reruns_clean(
    cutover: ModuleType, production_shape: str
) -> None:
    plan = cutover.dry_check(production_shape)
    assert plan is not None
    report = cutover.apply_and_reconcile(production_shape, _apply(production_shape))
    assert _changed(report) == {
        (date(2025, 1, 9), "minute", "ABC", 1, 0),
        (date(2021, 12, 31), "minute", "ABC", 0, 1),
        (date(2021, 12, 31), "daily", "ABC", 0, 1),
    }
    assert "already applied" not in cutover.report_text("v0", plan, report)

    assert cutover.dry_check(production_shape) is None
    again = cutover.apply_and_reconcile(production_shape, _apply(production_shape))
    assert again.is_noop()


def test_migration_applied_elsewhere_still_reconciles(
    cutover: ModuleType, production_shape: str
) -> None:
    _migrate(production_shape, MINUTE_MIGRATIONS)  # `mt data migrate apply` alone

    def must_not_apply() -> None:
        raise AssertionError("the migration was already applied")

    assert cutover.dry_check(production_shape) is None
    report = cutover.apply_and_reconcile(production_shape, must_not_apply)
    assert (date(2025, 1, 9), "minute", "ABC", 1, 0) in _changed(report)
    assert "already applied" in cutover.report_text("v0", None, report)


def test_unpinned_plan_stops_before_any_write(
    cutover: ModuleType, production_shape: str
) -> None:
    with psycopg.connect(production_shape) as conn:
        conn.execute(
            "DELETE FROM trading_sessions WHERE session_date = '2024-03-04' "
            "AND calendar_id = 'NYSE'"
        )
    with pytest.raises(cutover.CutoverError, match="differs"):
        cutover.dry_check(production_shape)
    with psycopg.connect(production_shape) as conn:
        assert not cutover.is_applied(conn)
        gaps = conn.execute("SELECT count(*) FROM data_gaps").fetchone()
    assert gaps == (1,)
