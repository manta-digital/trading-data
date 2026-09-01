"""
DaemonHeartbeat: status enum, dataclass, and repository.

The heartbeat table holds at most one row per daemon_id — the daemon upserts
on every status transition. The CLI status command reads this row to determine
whether the daemon is alive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from manta_trading.data.acquisition.daemon.types import (
    HEARTBEAT_ALIVE_THRESHOLD_SECONDS,
)


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class DaemonStatus(StrEnum):
    """Lifecycle status values written to the daemon_heartbeat table.

    SQL cross-reference: daemon_heartbeat.status column.
    All code that reads or writes a status value MUST reference this enum —
    no bare string literals.
    """

    STARTING = "STARTING"
    WORKING = "WORKING"
    IDLE = "IDLE"
    CYCLE_COMPLETE = "CYCLE_COMPLETE"
    STOPPED = "STOPPED"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class DaemonHeartbeat:
    """One row from the daemon_heartbeat table.

    Fields mirror table columns exactly. Nullable columns use ``... | None``.
    """

    daemon_id: str
    status: DaemonStatus
    started_at: datetime
    last_beat_at: datetime
    current_symbol: str | None = None
    cycle_count: int = 0
    pid: int | None = None
    hostname: str | None = None


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

_COLS = (
    "daemon_id, status, started_at, last_beat_at, "
    "current_symbol, cycle_count, pid, hostname"
)


def _row_to_heartbeat(row: dict) -> DaemonHeartbeat:
    return DaemonHeartbeat(
        daemon_id=row["daemon_id"],
        status=DaemonStatus(row["status"]),
        started_at=row["started_at"],
        last_beat_at=row["last_beat_at"],
        current_symbol=row["current_symbol"],
        cycle_count=row["cycle_count"],
        pid=row["pid"],
        hostname=row["hostname"],
    )


class HeartbeatRepository:
    """Read/write access to the daemon_heartbeat table.

    Uses a psycopg3 ConnectionPool. All SQL is parameterized.

    Args:
        pool: An open psycopg3 ConnectionPool pointing at TimescaleDB.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert(self, heartbeat: DaemonHeartbeat) -> None:
        """Insert or update the heartbeat row for daemon_id.

        Uses ``INSERT ... ON CONFLICT (daemon_id) DO UPDATE`` so there is
        always exactly one row per daemon identity.
        """
        sql = """
            INSERT INTO daemon_heartbeat (
                daemon_id, status, started_at, last_beat_at,
                current_symbol, cycle_count, pid, hostname
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (daemon_id) DO UPDATE SET
                status         = EXCLUDED.status,
                started_at     = EXCLUDED.started_at,
                last_beat_at   = EXCLUDED.last_beat_at,
                current_symbol = EXCLUDED.current_symbol,
                cycle_count    = EXCLUDED.cycle_count,
                pid            = EXCLUDED.pid,
                hostname       = EXCLUDED.hostname
        """
        params = (
            heartbeat.daemon_id,
            str(heartbeat.status),
            heartbeat.started_at,
            heartbeat.last_beat_at,
            heartbeat.current_symbol,
            heartbeat.cycle_count,
            heartbeat.pid,
            heartbeat.hostname,
        )
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)

    def get(self, daemon_id: str) -> DaemonHeartbeat | None:
        """Fetch the heartbeat row for daemon_id. Returns None if absent."""
        sql = f"SELECT {_COLS} FROM daemon_heartbeat WHERE daemon_id = %s"
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (daemon_id,))
                row = cur.fetchone()
        return _row_to_heartbeat(row) if row is not None else None

    def is_alive(
        self,
        heartbeat: DaemonHeartbeat | None,
        *,
        threshold_seconds: int = HEARTBEAT_ALIVE_THRESHOLD_SECONDS,
    ) -> bool:
        """Return True iff the daemon wrote a heartbeat within threshold_seconds.

        Args:
            heartbeat: The heartbeat row, or None if no row exists.
            threshold_seconds: Max age of last_beat_at to be considered alive.

        Returns:
            True if alive, False if absent or stale.
        """
        if heartbeat is None:
            return False
        now = datetime.now(timezone.utc)
        # Ensure comparison is timezone-aware on both sides
        beat = heartbeat.last_beat_at
        if beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        age = (now - beat).total_seconds()
        return age <= threshold_seconds
