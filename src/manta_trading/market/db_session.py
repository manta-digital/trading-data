"""Shared psycopg3 pool ``configure`` hook for session settings.

Hoisted from ``api_server.app`` so the acquisition daemon's pools can apply
the same ``DbSessionSettings`` contract (slice 186 D1) without importing the
serving layer. Every pool that talks to TimescaleDB should install one of
these hooks: a session without a ``statement_timeout`` runs a pathological
query forever (journal 20260806 — the daemon's mode query wedged for 15+
hours against an over-chunked hypertable because its pool set no timeout).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql

from manta_trading.constants import DbSessionSettings


def session_statements(session: DbSessionSettings) -> list[sql.Composed]:
    """The ``SET`` statements a session budget expands to, in order.

    Shared by the sync pool hook below and async consumers that cannot run
    the hook (slice 262's ``data/kalshi/db.py``), so the list exists once.
    ``SET`` takes no bind parameters; the values are quoted as literals.
    """
    return [
        sql.SQL("SET timezone = {}").format(sql.Literal("UTC")),
        sql.SQL("SET work_mem = {}").format(sql.Literal(session.work_mem)),
        sql.SQL("SET statement_timeout = {}").format(
            sql.Literal(session.statement_timeout)
        ),
    ]


def make_configure_connection(
    session: DbSessionSettings,
) -> Callable[[psycopg.Connection[Any]], None]:
    """Build a pool ``configure`` hook for a given session budget.

    Sets UTC timezone plus the two workload-shaped values carried by
    ``DbSessionSettings`` (``work_mem``, ``statement_timeout``). Autocommit is
    toggled so ``SET`` does not leave the connection in INTRANS state, which
    psycopg3's ``ConnectionPool`` rejects.

    A factory rather than a module function because ``statement_timeout`` is
    operator-settable for some consumers (186 D9) and must be resolved at
    startup.
    """

    def configure(conn: psycopg.Connection[Any]) -> None:
        conn.autocommit = True
        for statement in session_statements(session):
            conn.execute(statement)
        conn.autocommit = False

    return configure
