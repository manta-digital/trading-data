"""The tick store context: preflight, one connection, one advisory lock (225).

LLD 225 Technical Decision 1. The provider-free half of 223's run context:
ingest makes no provider call, and must run on a host that holds no
Databento key. :func:`open_tick_store` refuses — :class:`TickPreflightError`,
exit 1 at the CLI, naming the variable or command to fix — in 223's order:

1. an ``MT_TICK_*`` key in the environment or the ``.env`` file that is not a
   tick setting (``Settings`` ignores unknown keys, so a misspelt ceiling
   would otherwise read as "no ceiling");
2. ``MT_TICK_DB_URL`` is unset;
3. ``MT_TICK_ARCHIVE_DIR`` is unset, not an existing directory, or not
   writable (never created: a typo must not build a second archive);
4. the tick database is unreachable within the connect timeout;
5. a tick migration is pending;
6. another run holds the given lock key.

``open_tick_run`` (``run_context.py``) composes this with the provider.
Status and coverage take no lock and need no archive directory: they use
:func:`database_url` and :func:`connect_migrated` directly.

The shape is Kalshi's ``open_sync_connection``, copied, not imported (the
tick package never imports ``data.kalshi``): one autocommit
``psycopg.AsyncConnection`` whose explicit transactions hold every write, and
a session-level lock released when the connection closes, crash included.
"""

from __future__ import annotations

import difflib
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from dotenv import dotenv_values
from psycopg import errors

from manta_trading.config import ENV_FILE, Settings
from manta_trading.data.tick.constants import (
    TICK_ACQUISITION_LOCK_KEY,
    TICK_ARCHIVE_DIR_ENV,
    TICK_DB_CONNECT_TIMEOUT_SECONDS,
    TICK_ENV_PREFIX,
    TICK_INGEST_LOCK_KEY,
)
from manta_trading.market.schema.databases import (
    Credential,
    Database,
    DatabaseNotConfiguredError,
    env_var_for,
    resolve_database_url,
)
from manta_trading.market.schema.migrations import TRACKS

#: Operator-facing remedies, defined once.
TRACK_NOT_APPLIED = (
    "tick track has pending migrations: {missing} — mt data migrate apply --track tick"
)
#: What each tick lock serializes, for the "lock held" refusal.
LOCK_NAMES = {TICK_ACQUISITION_LOCK_KEY: "acquisition", TICK_INGEST_LOCK_KEY: "ingest"}
_TICK_FIELD_PREFIX = "tick_"

Clock = Callable[[], datetime]


class TickPreflightError(Exception):
    """The run cannot start; the message says what an operator must fix."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def lock_held_message(lock_key: int) -> str:
    return f"another tick {LOCK_NAMES[lock_key]} run holds the lock"


@dataclass(frozen=True)
class TickStore:
    """What every provider-free tick verb works with, for one run."""

    settings: Settings
    conn: psycopg.AsyncConnection[Any]
    archive_root: Path
    run_id: uuid.UUID
    clock: Clock


def known_tick_env_names() -> frozenset[str]:
    """Environment names of every ``tick_*`` setting, from ``Settings`` itself."""
    prefix = Settings.model_config["env_prefix"]
    return frozenset(
        f"{prefix}{field}".upper()
        for field in Settings.model_fields
        if field.startswith(_TICK_FIELD_PREFIX)
    )


def check_env_keys(env_file: Path | None) -> None:
    """Refuse an ``MT_TICK_*`` key no setting reads, naming the closest one."""
    names = set(os.environ)
    if env_file is not None and env_file.is_file():
        names |= set(dotenv_values(env_file))
    known = known_tick_env_names()
    unknown = sorted(
        name
        for name in names
        if name.upper().startswith(TICK_ENV_PREFIX) and name.upper() not in known
    )
    if not unknown:
        return
    hints = []
    for name in unknown:
        close = difflib.get_close_matches(name.upper(), sorted(known), n=1)
        hints.append(f"{name} (did you mean {close[0]}?)" if close else name)
    raise TickPreflightError(
        f"unknown tick setting {', '.join(hints)}; known: {', '.join(sorted(known))}"
    )


def _archive_root(settings: Settings) -> Path:
    root = settings.tick_archive_dir
    if root is None:
        raise TickPreflightError(f"{TICK_ARCHIVE_DIR_ENV} is not set")
    if not root.is_dir():
        raise TickPreflightError(
            f"{TICK_ARCHIVE_DIR_ENV}={root} is not an existing directory "
            "(it is never created implicitly)"
        )
    if not os.access(root, os.W_OK | os.X_OK):
        raise TickPreflightError(f"{TICK_ARCHIVE_DIR_ENV}={root} is not writable")
    return root


def database_url(settings: Settings) -> str:
    """The tick database's application URL; refuses when it is unset."""
    try:
        return resolve_database_url(settings, Database.TICK, Credential.APPLICATION)
    except DatabaseNotConfiguredError as exc:
        raise TickPreflightError(f"{exc.env_var} is not set") from exc


async def _connect(url: str) -> psycopg.AsyncConnection[Any]:
    try:
        return await psycopg.AsyncConnection.connect(
            url, connect_timeout=TICK_DB_CONNECT_TIMEOUT_SECONDS, autocommit=True
        )
    except psycopg.OperationalError as exc:
        env_var = env_var_for(Database.TICK, Credential.APPLICATION)
        raise TickPreflightError(
            f"tick database ({env_var}) unreachable within "
            f"{TICK_DB_CONNECT_TIMEOUT_SECONDS} s: {exc}"
        ) from exc


async def _scalar(
    conn: psycopg.AsyncConnection[Any],
    query: LiteralString,
    params: tuple[object, ...] = (),
) -> Any:
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    return row[0] if row else None


async def _missing_migrations(conn: psycopg.AsyncConnection[Any]) -> list[str]:
    """Ids in ``TRACKS["tick"]`` absent from the ledger, in track order."""
    expected = [migration["id"] for migration in TRACKS["tick"]]
    try:
        cursor = await conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ANY(%s)",
            (expected,),
        )
        rows = await cursor.fetchall()
    except errors.UndefinedTable:
        # A bare database has no ledger (the bootstrap migration creates it):
        # every id is pending, and the remedy is the same apply command.
        return expected
    applied = {row[0] for row in rows}
    return [migration_id for migration_id in expected if migration_id not in applied]


async def connect_migrated(url: str) -> psycopg.AsyncConnection[Any]:
    """Connect and require every tick migration; the caller closes it."""
    conn = await _connect(url)
    try:
        missing = await _missing_migrations(conn)
        if missing:
            raise TickPreflightError(
                TRACK_NOT_APPLIED.format(missing=", ".join(missing))
            )
    except BaseException:
        await conn.close()
        raise
    return conn


async def _locked_connection(url: str, lock_key: int) -> psycopg.AsyncConnection[Any]:
    """:func:`connect_migrated`, then take the run's advisory lock."""
    conn = await connect_migrated(url)
    try:
        if not await _scalar(conn, "SELECT pg_try_advisory_lock(%s)", (lock_key,)):
            raise TickPreflightError(lock_held_message(lock_key))
    except BaseException:
        await conn.close()
        raise
    return conn


@asynccontextmanager
async def open_tick_store(
    settings: Settings,
    *,
    lock_key: int,
    clock: Clock = utc_now,
    env_file: Path | None = Path(ENV_FILE),
) -> AsyncIterator[TickStore]:
    """Run the preflight in TD1's order, then yield the store; release on exit."""
    check_env_keys(env_file)
    url = database_url(settings)
    archive_root = _archive_root(settings)
    conn = await _locked_connection(url, lock_key)
    try:
        yield TickStore(
            settings=settings,
            conn=conn,
            archive_root=archive_root,
            run_id=uuid.uuid4(),
            clock=clock,
        )
    finally:
        await conn.close()
