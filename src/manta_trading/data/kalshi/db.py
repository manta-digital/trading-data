"""Preflight for the catalog sync: one async connection, checked and locked.

Decision 8/11: the sync holds **one** ``psycopg.AsyncConnection`` for the
run — no pool — opened with the application credential. Preflight, in
order: connect within ``DB_CONNECT_TIMEOUT_SECONDS``; apply the
``DB_BULK_SESSION`` settings (the same ``SET`` list the sync pool hook
issues); verify ``kalshi.sync_state`` exists; take the session-level
advisory lock so two syncs never write concurrently. Any failure is a
:class:`PreflightError` (exit 1 at the CLI). The connection stays in
autocommit mode: every write the sync makes is inside an explicit
``transaction()`` block, so nothing is left in an implicit open
transaction. Closing the connection releases the lock.
"""

from __future__ import annotations

from typing import Any

import psycopg

from manta_trading.constants import DB_BULK_SESSION
from manta_trading.data.kalshi.constants import (
    DB_CONNECT_TIMEOUT_SECONDS,
    SYNC_ADVISORY_LOCK_KEY,
)
from manta_trading.market.db_session import session_statements

#: Operator-facing remedies, defined once.
TRACK_NOT_APPLIED = (
    "kalshi.sync_state does not exist — apply the kalshi track "
    "(mt data migrate apply --track kalshi)"
)
LOCK_HELD = "another kalshi sync holds the run lock"


class PreflightError(Exception):
    """The sync cannot start; the message says what an operator must fix."""


async def open_sync_connection(url: str) -> psycopg.AsyncConnection[Any]:
    """Connect, configure, verify the schema, and take the run lock."""
    try:
        conn = await psycopg.AsyncConnection.connect(
            url, connect_timeout=DB_CONNECT_TIMEOUT_SECONDS, autocommit=True
        )
    except psycopg.OperationalError as exc:
        raise PreflightError(f"database unreachable: {exc}") from exc
    try:
        for statement in session_statements(DB_BULK_SESSION):
            await conn.execute(statement)
        if await _scalar(conn, "SELECT to_regclass('kalshi.sync_state')") is None:
            raise PreflightError(TRACK_NOT_APPLIED)
        locked = await _scalar(
            conn, "SELECT pg_try_advisory_lock(%s)", (SYNC_ADVISORY_LOCK_KEY,)
        )
        if not locked:
            raise PreflightError(LOCK_HELD)
    except BaseException:
        await conn.close()
        raise
    return conn


async def _scalar(
    conn: psycopg.AsyncConnection[Any], query: str, params: tuple[object, ...] = ()
) -> Any:
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    return row[0] if row else None
