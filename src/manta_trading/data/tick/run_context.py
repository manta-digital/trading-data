"""The tick run context: preflight, one connection, one advisory lock (223).

LLD 224 Technical Decision 2. Every manifest writer (``adopt``, ``reset``,
224's ``pass``) runs inside :func:`open_tick_run`. Before it yields, it
refuses — :class:`TickPreflightError`, exit 1 at the CLI, naming the variable
or command to fix — in this order:

1. an ``MT_TICK_*`` key in the environment or the ``.env`` file that is not a
   tick setting (``Settings`` ignores unknown keys, so a misspelt ceiling
   would otherwise read as "no ceiling");
2. the Databento API key is unset;
3. ``MT_TICK_DB_URL`` is unset;
4. ``MT_TICK_ARCHIVE_DIR`` is unset, not an existing directory, or not
   writable (never created: a typo must not build a second archive);
5. the tick database is unreachable within the connect timeout;
6. a tick migration is pending;
7. another run holds ``TICK_ACQUISITION_LOCK_KEY``.

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
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, LiteralString, Protocol

import psycopg
from dotenv import dotenv_values
from psycopg import errors

from manta_trading.config import ENV_FILE, Settings
from manta_trading.data.tick.constants import (
    TICK_ACQUISITION_LOCK_KEY,
    TICK_ARCHIVE_DIR_ENV,
    TICK_DB_CONNECT_TIMEOUT_SECONDS,
    TICK_ENV_PREFIX,
)
from manta_trading.data.tick.provider import (
    ITickAcquisitionProvider,
    ITickMetadataProvider,
)
from manta_trading.market.schema.databases import (
    Credential,
    Database,
    DatabaseNotConfiguredError,
    env_var_for,
    resolve_database_url,
)
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.providers.errors import ProviderAuthError

#: Operator-facing remedies, defined once.
TRACK_NOT_APPLIED = (
    "tick track has pending migrations: {missing} — mt data migrate apply --track tick"
)
LOCK_HELD = "another tick acquisition run holds the lock"
_TICK_FIELD_PREFIX = "tick_"


class TickPreflightError(Exception):
    """The run cannot start; the message says what an operator must fix."""


class TickProvider(ITickMetadataProvider, ITickAcquisitionProvider, Protocol):
    """One provider instance per run, closed when the run ends (220 TD2)."""

    def __enter__(self) -> TickProvider: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        /,
    ) -> None: ...


ProviderFactory = Callable[[Settings], TickProvider]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TickRun:
    """What every tick verb works with, for the life of one run."""

    settings: Settings
    provider: TickProvider
    conn: psycopg.AsyncConnection[Any]
    archive_root: Path
    run_id: str
    clock: Clock


def known_tick_env_names() -> frozenset[str]:
    """Environment names of every ``tick_*`` setting, from ``Settings`` itself."""
    prefix = Settings.model_config["env_prefix"]
    return frozenset(
        f"{prefix}{field}".upper()
        for field in Settings.model_fields
        if field.startswith(_TICK_FIELD_PREFIX)
    )


def _check_env_keys(env_file: Path | None) -> None:
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


def _database_url(settings: Settings) -> str:
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


async def _checked_connection(url: str) -> psycopg.AsyncConnection[Any]:
    """Connect, require every tick migration, and take the run lock."""
    conn = await _connect(url)
    try:
        missing = await _missing_migrations(conn)
        if missing:
            raise TickPreflightError(
                TRACK_NOT_APPLIED.format(missing=", ".join(missing))
            )
        locked = await _scalar(
            conn, "SELECT pg_try_advisory_lock(%s)", (TICK_ACQUISITION_LOCK_KEY,)
        )
        if not locked:
            raise TickPreflightError(LOCK_HELD)
    except BaseException:
        await conn.close()
        raise
    return conn


def _default_provider(settings: Settings) -> TickProvider:
    # Imported here: ``databento`` adds ~0.15 s to every ``mt`` startup.
    from manta_trading.data.tick.databento.adapter import DatabentoTickProvider

    return DatabentoTickProvider.from_settings(settings)


@asynccontextmanager
async def open_tick_run(
    settings: Settings,
    *,
    clock: Clock = utc_now,
    provider_factory: ProviderFactory = _default_provider,
    env_file: Path | None = Path(ENV_FILE),
) -> AsyncIterator[TickRun]:
    """Run the preflight in TD2's order, then yield the run; release on exit."""
    _check_env_keys(env_file)
    try:
        provider = provider_factory(settings)
    except ProviderAuthError as exc:
        raise TickPreflightError(str(exc)) from exc
    async with AsyncExitStack() as stack:
        stack.enter_context(provider)
        url = _database_url(settings)
        archive_root = _archive_root(settings)
        conn = await _checked_connection(url)
        stack.push_async_callback(conn.close)
        yield TickRun(
            settings=settings,
            provider=provider,
            conn=conn,
            archive_root=archive_root,
            run_id=uuid.uuid4().hex,
            clock=clock,
        )
