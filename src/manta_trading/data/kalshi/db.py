"""Preflight for the Kalshi pass: one async connection, checked and locked.

Decision 8/11 (262): the pass holds **one** ``psycopg.AsyncConnection`` for
the run — no pool — opened with the application credential. Preflight, in
order: connect within ``DB_CONNECT_TIMEOUT_SECONDS``; apply the
``DB_BULK_SESSION`` settings (the same ``SET`` list the sync pool hook
issues); verify the kalshi migration ledger is complete (264 Decision 8:
every id in ``TRACKS["kalshi"]`` is recorded, so a deploy whose migration
was not applied exits 1 naming it rather than failing mid-phase); take the
session-level advisory lock so two passes never write concurrently. Any
failure is a :class:`PreflightError` (exit 1 at the CLI). The connection
stays in autocommit mode: every write the pass makes is inside an explicit
``transaction()`` block, so nothing is left in an implicit open
transaction. Closing the connection releases the lock.
"""

from __future__ import annotations

from typing import Any, LiteralString

import psycopg
from psycopg import errors

from manta_trading.constants import DB_BULK_SESSION
from manta_trading.data.kalshi.constants import (
    DB_CONNECT_TIMEOUT_SECONDS,
    SYNC_ADVISORY_LOCK_KEY,
)
from manta_trading.market.db_session import session_statements
from manta_trading.market.schema.migrations import TRACKS

#: Operator-facing remedies, defined once. ``{missing}`` is the
#: comma-separated list of unapplied migration ids.
TRACK_NOT_APPLIED = (
    "kalshi track has pending migrations: {missing} — "
    "mt data migrate apply --track kalshi"
)
LOCK_HELD = "another kalshi sync holds the run lock"


class PreflightError(Exception):
    """The pass cannot start; the message says what an operator must fix."""


async def open_sync_connection(url: str) -> psycopg.AsyncConnection[Any]:
    """Connect, configure, verify the ledger, and take the run lock."""
    try:
        conn = await psycopg.AsyncConnection.connect(
            url, connect_timeout=DB_CONNECT_TIMEOUT_SECONDS, autocommit=True
        )
    except psycopg.OperationalError as exc:
        raise PreflightError(f"database unreachable: {exc}") from exc
    try:
        for statement in session_statements(DB_BULK_SESSION):
            await conn.execute(statement)
        missing = await _missing_migrations(conn)
        if missing:
            raise PreflightError(TRACK_NOT_APPLIED.format(missing=", ".join(missing)))
        locked = await _scalar(
            conn, "SELECT pg_try_advisory_lock(%s)", (SYNC_ADVISORY_LOCK_KEY,)
        )
        if not locked:
            raise PreflightError(LOCK_HELD)
    except BaseException:
        await conn.close()
        raise
    return conn


async def _missing_migrations(conn: psycopg.AsyncConnection[Any]) -> list[str]:
    """Ids in ``TRACKS["kalshi"]`` absent from ``schema_migrations``, in
    track order — all of them, not just the first."""
    expected = [migration["id"] for migration in TRACKS["kalshi"]]
    try:
        cursor = await conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ANY(%s)",
            (expected,),
        )
        rows = await cursor.fetchall()
    except errors.UndefinedTable:
        # A bare database has no ledger at all (the bootstrap migration
        # creates it): every id is pending, and the remedy is the same
        # apply command — report that rather than a raw psycopg error.
        return expected
    applied = {row[0] for row in rows}
    return [migration_id for migration_id in expected if migration_id not in applied]


async def _scalar(
    conn: psycopg.AsyncConnection[Any],
    query: LiteralString,
    params: tuple[object, ...] = (),
) -> Any:
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    return row[0] if row else None
