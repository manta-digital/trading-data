"""The API's tick database: its pool, its ``503`` and its error mapping (231 TD2).

``lifespan`` calls :func:`open_tick_pool` and :func:`close_tick_pool`. The pool
opens without waiting for a connection, so ``mt-serve`` starts and keeps
serving equities and Kalshi with the tick database missing or down; psycopg_pool
retries in the background and logs each failed attempt at WARNING itself.

Every futures route depends on :func:`tick_connection`, which turns "not
configured", "unreachable" and "migrations pending" into
:class:`TickServiceUnavailable` before any planner or roll call runs. The app's
handler renders that as ``503 {"error": unavailable_text(...)}``. Health, status
and overview call :func:`probe_tick` / :func:`read_tick` instead, which return
the state rather than raise it. :func:`tick_errors` is the one mapping of read
failures for every futures route.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool
from starlette import status as http_status

from manta_trading.config import Settings
from manta_trading.constants import DbSessionSettings
from manta_trading.data.tick.constants import (
    TICK_API_CHECKOUT_TIMEOUT_SECONDS,
    TICK_API_POOL_MAX,
    TICK_API_POOL_MIN,
    TICK_DB_CONNECT_TIMEOUT_SECONDS,
)
from manta_trading.data.tick.get_plan import ContractNotFound
from manta_trading.data.tick.line_state import (
    OUTAGES,
    READ_FAILURES,
    TickLineState,
    TickUnavailable,
    classify,
    unavailable_text,
)
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.store_context import (
    TickPreflightError,
    missing_migrations,
)
from manta_trading.market.db_session import session_statements
from manta_trading.market.schema.databases import (
    Credential,
    Database,
    DatabaseNotConfiguredError,
    resolve_database_url,
)

__all__ = [
    "TickServiceUnavailable",
    "close_tick_pool",
    "open_tick_pool",
    "probe_tick",
    "read_tick",
    "tick_connection",
    "tick_errors",
]

_READ_ONLY = "SET default_transaction_read_only = on"


class TickServiceUnavailable(Exception):
    """The tick database cannot serve this request: rendered as a ``503``."""

    def __init__(self, unavailable: TickUnavailable) -> None:
        super().__init__(unavailable_text(unavailable))
        self.unavailable = unavailable


def _configure(
    session: DbSessionSettings,
) -> Callable[[Conn], Awaitable[None]]:
    """The serving session budget (186 D1), read-only, left in autocommit.

    Autocommit stays on: the per-checkout migration check then never leaves a
    transaction open, and a time-series read opens its own snapshot with
    ``conn.transaction()``.
    """

    async def configure(conn: Conn) -> None:
        await conn.set_autocommit(True)
        for statement in session_statements(session):
            await conn.execute(statement)
        await conn.execute(_READ_ONLY)

    return configure


async def _reset(conn: Conn) -> None:
    """Undo a read's snapshot settings before the connection is reused."""
    await conn.set_isolation_level(None)
    await conn.set_read_only(None)


async def open_tick_pool(
    app: FastAPI,
    tick_db_url: str | None,
    settings: Settings,
    calendar_url: str,
    session: DbSessionSettings,
) -> None:
    """Resolve the tick URL and open the pool without waiting (TD2).

    ``tick_db_url`` overrides ``MT_TICK_DB_URL`` (the load tier's seam, as
    ``db_url`` is for the primary). Unset leaves ``app.state.tick_pool`` None.
    """
    app.state.calendar_url = calendar_url
    app.state.tick_pool = None
    app.state.tick_url = None
    app.state.tick_unset = None
    try:
        url = tick_db_url or resolve_database_url(
            settings, Database.TICK, Credential.APPLICATION
        )
    except DatabaseNotConfiguredError as exc:
        app.state.tick_unset = f"{exc.env_var} unset"
        return
    pool: AsyncConnectionPool[Conn] = AsyncConnectionPool(
        url,
        open=False,
        min_size=TICK_API_POOL_MIN,
        max_size=TICK_API_POOL_MAX,
        timeout=TICK_API_CHECKOUT_TIMEOUT_SECONDS,
        configure=_configure(session),
        reset=_reset,
        kwargs={"connect_timeout": TICK_DB_CONNECT_TIMEOUT_SECONDS},
    )
    await pool.open(wait=False)
    app.state.tick_url = url
    app.state.tick_pool = pool


async def close_tick_pool(app: FastAPI) -> None:
    """Close the pool if there is one. Absent, not just ``None``, when
    :func:`open_tick_pool` failed before setting it: the lifespan's teardown
    must still run and let that failure through."""
    pool: AsyncConnectionPool[Conn] | None = getattr(app.state, "tick_pool", None)
    if pool is not None:
        await pool.close()


def _classify(app: FastAPI, exc: BaseException) -> TickUnavailable:
    """:func:`classify` with this app's tick password and statement timeout."""
    password = conninfo_to_dict(app.state.tick_url).get("password")
    return classify(
        exc,
        None if password is None else str(password),
        app.state.statement_timeout,
    )


async def _checkout(app: FastAPI) -> Conn:
    """A pooled tick connection whose track is migrated, or the reason not."""
    pool: AsyncConnectionPool[Conn] | None = app.state.tick_pool
    if pool is None:
        raise TickServiceUnavailable(
            TickUnavailable(TickLineState.NOT_CONFIGURED, app.state.tick_unset)
        )
    try:
        conn = await pool.getconn()
    except psycopg.OperationalError as exc:  # PoolTimeout included
        raise TickServiceUnavailable(_classify(app, exc)) from exc
    try:
        unavailable = await _migration_state(app, conn)
    except BaseException:
        # A cancelled request, or anything unforeseen: the connection goes
        # back before the error continues, or a pool of four leaks dry.
        await pool.putconn(conn)
        raise
    if unavailable is not None:
        await pool.putconn(conn)
        raise TickServiceUnavailable(unavailable)
    return conn


async def _migration_state(app: FastAPI, conn: Conn) -> TickUnavailable | None:
    """Why the tick track cannot serve on ``conn``, or ``None`` when it can."""
    try:
        missing = await missing_migrations(conn)
    except psycopg.Error as exc:
        # Reported, not raised: the caller turns it into the 503 line.
        return _classify(app, exc)
    if missing:
        return TickUnavailable(TickLineState.MIGRATIONS_PENDING, ", ".join(missing))
    return None


async def tick_connection(request: Request) -> AsyncIterator[Conn]:
    """FastAPI dependency: a checked tick connection held for the request.

    Holding it is what bounds the roll path's own connections: at most
    ``TICK_API_POOL_MAX`` requests per worker can be inside it (TD2).
    """
    app = request.app
    conn = await _checkout(app)
    try:
        yield conn
    finally:
        await app.state.tick_pool.putconn(conn)


async def probe_tick(app: FastAPI) -> TickUnavailable | None:
    """``tick_connection``'s checks, returned rather than raised (health)."""
    try:
        conn = await _checkout(app)
    except TickServiceUnavailable as exc:
        # Reported, not raised: health states the tick line and stays 200.
        return exc.unavailable
    await app.state.tick_pool.putconn(conn)
    return None


async def read_tick[T](
    app: FastAPI, read: Callable[[Conn], Awaitable[T]]
) -> T | TickUnavailable:
    """Run ``read`` on a checked connection; any failure becomes the line
    (status and overview never answer ``5xx`` for the tick tier)."""
    try:
        conn = await _checkout(app)
    except TickServiceUnavailable as exc:
        # Reported, not raised: the composed surface prints the state.
        return exc.unavailable
    try:
        return await read(conn)
    except READ_FAILURES as exc:
        # Reported, not raised (230 TD8): the line says why it has no figures.
        return _classify(app, exc)
    finally:
        await app.state.tick_pool.putconn(conn)


@contextmanager
def tick_errors(app: FastAPI) -> Iterator[None]:
    """Map a futures route's read failures (TD2). Order matters:
    ``QueryCanceled`` subclasses ``OperationalError`` and keeps its ``504``."""
    try:
        yield
    except psycopg.errors.QueryCanceled:
        raise  # the app's QueryCanceled handler answers 504
    except OUTAGES as exc:
        raise TickServiceUnavailable(_classify(app, exc)) from exc
    except ContractNotFound as exc:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TickPreflightError as exc:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from exc


def tick_unavailable_body(exc: TickServiceUnavailable) -> dict[str, Any]:
    """The ``503`` body: the one error shape (186 D6)."""
    return {"error": unavailable_text(exc.unavailable)}
