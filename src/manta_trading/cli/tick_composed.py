"""Open the tick database for a CLI composed surface (slice 230, TD8).

``mt data accounting`` and ``mt data overview`` (and 234's ``health``) read
the tick database for one line. :func:`open` returns a connection or a
:class:`TickUnavailable` saying why not; :func:`classify` turns a failure
during the read itself into the same states; :func:`read_line` composes the
two for a synchronous command. A tick outage never fails the command: the
line says so, and the command keeps its own exit rules.

The connection is bounded (``TICK_COMPOSED_CONNECT_TIMEOUT_SECONDS``, a
``statement_timeout`` of ``TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS``) and
read-only, so a hung tick cluster cannot stall the screen. No detail ever
carries the URL's password.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from manta_trading.config import Settings
from manta_trading.data.tick.constants import (
    TICK_COMPOSED_CONNECT_TIMEOUT_SECONDS,
    TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS,
)
from manta_trading.data.tick.line_state import TickLineState, TickUnavailable
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.store_context import (
    TickPreflightError,
    missing_migrations,
)
from manta_trading.data.tick.tick_calendar import TickCalendarError
from manta_trading.market.schema.databases import (
    Credential,
    Database,
    DatabaseNotConfiguredError,
    resolve_database_url,
)

_SCRUBBED = "***"
#: What a composed read can raise once connected: the tick database dropping
#: or timing out (``QueryCanceled`` is an ``OperationalError``), or the
#: calendar it reads sessions from failing or refusing the range. Each is the
#: line's ``unreachable`` state, never a failed command (FR14).
_READ_FAILURES = (psycopg.OperationalError, TickCalendarError, TickPreflightError)


def _first_line(exc: BaseException, password: str | None) -> str:
    lines = str(exc).strip().splitlines()
    text = lines[0] if lines else type(exc).__name__
    return text.replace(password, _SCRUBBED) if password else text


def _password(settings: Settings) -> str | None:
    try:
        url = resolve_database_url(settings, Database.TICK, Credential.APPLICATION)
    except DatabaseNotConfiguredError:
        return None
    password = conninfo_to_dict(url).get("password")
    return None if password is None else str(password)


def classify(exc: Exception, settings: Settings) -> TickUnavailable:
    """A connect or read failure as the ``unreachable`` line; a cancelled
    statement names the timeout. The URL's password is scrubbed."""
    detail = _first_line(exc, _password(settings))
    if isinstance(exc, psycopg.errors.QueryCanceled):
        detail = (
            f"statement timeout ({TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS} s): {detail}"
        )
    return TickUnavailable(TickLineState.UNREACHABLE, detail)


async def open(settings: Settings) -> Conn | TickUnavailable:
    """A bounded, read-only, autocommit tick connection, or why there is none.
    The caller closes the connection."""
    try:
        url = resolve_database_url(settings, Database.TICK, Credential.APPLICATION)
    except DatabaseNotConfiguredError as exc:
        return TickUnavailable(TickLineState.NOT_CONFIGURED, f"{exc.env_var} unset")
    conninfo = make_conninfo(
        url,
        connect_timeout=TICK_COMPOSED_CONNECT_TIMEOUT_SECONDS,
        options=(
            f"-c statement_timeout={TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS}s"
            " -c default_transaction_read_only=on"
        ),
    )
    try:
        conn = await psycopg.AsyncConnection.connect(conninfo, autocommit=True)
    except psycopg.OperationalError as exc:
        return classify(exc, settings)
    try:
        missing = await missing_migrations(conn)
    except psycopg.OperationalError as exc:
        await conn.close()
        return classify(exc, settings)
    if missing:
        await conn.close()
        return TickUnavailable(TickLineState.MIGRATIONS_PENDING, ", ".join(missing))
    return conn


async def _read[T](
    settings: Settings, read: Callable[[Conn], Awaitable[T]]
) -> T | TickUnavailable:
    opened = await open(settings)
    if isinstance(opened, TickUnavailable):
        return opened
    try:
        return await read(opened)
    except _READ_FAILURES as exc:
        return classify(exc, settings)
    finally:
        await opened.close()


def read_line[T](
    settings: Settings, read: Callable[[Conn], Awaitable[T]]
) -> T | TickUnavailable:
    """Run ``read`` on a composed connection from a synchronous command."""
    return asyncio.run(_read(settings, read))
