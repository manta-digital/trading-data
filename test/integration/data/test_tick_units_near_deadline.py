"""``units_near_deadline``: what ``tick retention`` names (slice 234 TD5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
from tick_support.rows import insert_request, insert_unit
from tick_support.runs import connect

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import TICK_RETENTION_WARN_DAYS, UnitState
from manta_trading.data.tick.manifest_request_reads import units_near_deadline

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
WINDOW = timedelta(days=TICK_RETENTION_WARN_DAYS)


def _seed(conn: psycopg.Connection, job: str, days: float, **unit: object) -> None:
    request_id = insert_request(
        conn, provider_job_id=job, download_deadline=NOW + timedelta(days=days)
    )
    insert_unit(conn, request_id, **({"state": UnitState.SUBMITTED.value} | unit))


async def test_only_in_window_in_flight_open_units_are_named(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        _seed(conn, "SOON", 3)
        _seed(conn, "LATER", 8)
        _seed(conn, "PAST", -2, state=UnitState.DELIVERED.value)
        _seed(
            conn,
            "HOLED",
            3,
            fetch_status=FetchStatus.PROVIDER_HOLE.value,
            failure_reason="no file delivered",
        )
        _seed(conn, "REOPENED", 3, reopened_at=NOW - timedelta(days=1))
        _seed(conn, "REQUESTED", 3, state=UnitState.REQUESTED.value)
    async with connect(migrated_tick_db) as aconn:
        found = await units_near_deadline(aconn, NOW, WINDOW)
    assert [(f.job_id, f.deadline, f.units) for f in found] == [
        ("PAST", NOW - timedelta(days=2), 1),
        ("SOON", NOW + timedelta(days=3), 1),
    ]
