"""Integration tests: the catalog sync end to end on a throwaway database
(slice 262, Tasks 8.2, 8.4, 8.5).

Fake source, real repository, real connection preflight — only
``ephemeral_db`` (``MT_TIMESCALE_TEST_URL``), never the production URL.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from manta_trading.constants import DB_BULK_SESSION
from manta_trading.data.kalshi.constants import SYNC_ADVISORY_LOCK_KEY
from manta_trading.data.kalshi.db import (
    LOCK_HELD,
    TRACK_NOT_APPLIED,
    PreflightError,
    open_sync_connection,
)

# ---------------------------------------------------------------------------
# Task 8.2 — preflight
# ---------------------------------------------------------------------------


class TestPreflight:
    async def test_session_settings_applied_and_lock_taken(self, kalshi_db: str):
        conn = await open_sync_connection(kalshi_db)
        try:
            # SHOW normalizes units ("300s" → "5min"); compare in seconds.
            cursor = await conn.execute(
                "SELECT extract(epoch FROM "
                "current_setting('statement_timeout')::interval)"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert int(row[0]) == int(DB_BULK_SESSION.statement_timeout.rstrip("s"))
            cursor = await conn.execute("SHOW timezone")
            assert await cursor.fetchone() == ("UTC",)
            assert conn.autocommit is True
            cursor = await conn.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND objid = %s AND pid = pg_backend_pid()",
                (SYNC_ADVISORY_LOCK_KEY,),
            )
            assert await cursor.fetchone() == (1,)
        finally:
            await conn.close()

    async def test_unmigrated_database_names_the_track(self, ephemeral_db: str):
        with pytest.raises(PreflightError, match="kalshi track"):
            await open_sync_connection(ephemeral_db)
        assert "kalshi track" in TRACK_NOT_APPLIED

    async def test_held_lock_is_refused(self, kalshi_db: str):
        holder = await open_sync_connection(kalshi_db)
        try:
            with pytest.raises(PreflightError, match=LOCK_HELD):
                await open_sync_connection(kalshi_db)
        finally:
            await holder.close()
        # Closing the holder releases the lock: the next preflight succeeds.
        again = await open_sync_connection(kalshi_db)
        await again.close()

    async def test_unreachable_database(self):
        with pytest.raises(PreflightError, match="unreachable"):
            await open_sync_connection(
                "postgresql://nobody:none@127.0.0.1:1/nothing?connect_timeout=1"
            )


async def _pid(conn: psycopg.AsyncConnection[Any]) -> int:
    cursor = await conn.execute("SELECT pg_backend_pid()")
    row = await cursor.fetchone()
    assert row is not None
    return int(row[0])
