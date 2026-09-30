"""Plan and run the ingest worker against real databases (slice 225 tests).

The tick database is a migrated test database; the calendar is
``session_migrated_db``. Reads use plain sync connections: fixtures and
assertions, not the code under test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from tick_support.runs import connect

from manta_trading.data.tick.constants import (
    TICK_DB_CONNECT_TIMEOUT_SECONDS,
    TICK_DB_KEEPALIVES_COUNT,
    TICK_DB_KEEPALIVES_IDLE_SECONDS,
    TICK_DB_KEEPALIVES_INTERVAL_SECONDS,
)
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.ingest_plan import Calendars, UnitIngestPlan, build_plan
from manta_trading.data.tick.ingest_worker import (
    UnitOutcome,
    WorkerConnectionSettings,
    ingest_unit,
)
from manta_trading.data.tick.manifest_reads import units_by_id

INGESTED_AT = datetime(2026, 9, 30, 12, tzinfo=UTC)
#: Long enough never to fire in a test that is not about it.
TEST_LOCK_TIMEOUT_SECONDS = 30.0


def worker_settings(
    url: str, lock_timeout_seconds: float = TEST_LOCK_TIMEOUT_SECONDS
) -> WorkerConnectionSettings:
    return WorkerConnectionSettings(
        url=url,
        connect_timeout_seconds=TICK_DB_CONNECT_TIMEOUT_SECONDS,
        keepalives_idle_seconds=TICK_DB_KEEPALIVES_IDLE_SECONDS,
        keepalives_interval_seconds=TICK_DB_KEEPALIVES_INTERVAL_SECONDS,
        keepalives_count=TICK_DB_KEEPALIVES_COUNT,
        lock_timeout_seconds=lock_timeout_seconds,
    )


async def plan_unit(url: str, calendars: Calendars, unit_id: int) -> UnitIngestPlan:
    async with connect(url) as conn:
        (unit,) = await units_by_id(conn, [unit_id])
        return await build_plan(conn, calendars, unit)


def run_worker(
    plan: UnitIngestPlan,
    archive: Path,
    url: str,
    lock_timeout_seconds: float = TEST_LOCK_TIMEOUT_SECONDS,
) -> UnitOutcome:
    return ingest_unit(
        plan,
        archive,
        worker_settings(url, lock_timeout_seconds),
        DbnFileReader(),
        lambda: INGESTED_AT,
    )


def scalar(url: str, query: str, *params: Any) -> Any:
    with psycopg.connect(url) as conn:
        row = conn.execute(query, params).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return row[0]


def unit_state(url: str, unit_id: int) -> tuple[Any, ...]:
    """``(state, fetch_status, decoded_record_count, superseded_by_unit_id)``."""
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT state, fetch_status, decoded_record_count, superseded_by_unit_id"
            " FROM tick_archive_unit WHERE unit_id = %s",
            (unit_id,),
        ).fetchone()
    assert row is not None
    return tuple(row)


def trade_count(url: str, unit_id: int) -> int:
    return int(
        scalar(url, "SELECT count(*) FROM tick_trade WHERE unit_id = %s", unit_id)
    )


def ledger_count(url: str, unit_id: int) -> int:
    return int(
        scalar(
            url, "SELECT count(*) FROM tick_ingest_ledger WHERE unit_id = %s", unit_id
        )
    )
