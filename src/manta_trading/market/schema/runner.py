"""Schema migration runner — track-agnostic apply and state query functions.

Both functions accept a psycopg3 ``ConnectionPool`` and a migration list,
so they work with any DB that uses that pool type.
"""

from __future__ import annotations

from typing import Any

import psycopg as _psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_TABLE_EXISTS_SQL = (
    "SELECT EXISTS ("
    "  SELECT 1 FROM information_schema.tables "
    "  WHERE table_name = 'schema_migrations'"
    ") AS table_exists"
)


def _schema_migrations_table_exists(pool: ConnectionPool) -> bool:
    """Return True if the schema_migrations table is present on this connection pool."""
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_TABLE_EXISTS_SQL)
            row = cur.fetchone()
        conn.commit()
    return bool(row and row["table_exists"])


def apply_migrations(
    pool: ConnectionPool, migrations: list[dict[str, Any]]
) -> list[str]:
    """Apply pending migrations; return IDs of newly applied ones.

    Each migration runs in its own transaction. A failure mid-sequence
    leaves prior migrations committed and raises the underlying exception.

    Bootstrap: if the ``schema_migrations`` table does not exist, the first
    migration (``001_schema_migrations``) is run and recorded before the
    normal apply loop starts.
    """
    if not _schema_migrations_table_exists(pool):
        bootstrap = next(m for m in migrations if m["id"] == "001_schema_migrations")
        with pool.connection() as conn:
            conn.execute(bootstrap["sql"])
            conn.execute(
                "INSERT INTO schema_migrations (migration_id, description) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (bootstrap["id"], bootstrap["description"]),
            )
            conn.commit()
        _logger.info("Bootstrapped schema_migrations table")

    # Determine already-applied migrations
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT migration_id FROM schema_migrations")
            applied = {r["migration_id"] for r in cur.fetchall()}
        conn.commit()

    # Apply each pending migration in order
    newly_applied: list[str] = []
    for migration in migrations:
        if migration["id"] in applied:
            continue
        sql = migration.get("sql", "")
        python_fn = migration.get("python_fn")
        requires_autocommit = migration.get("requires_autocommit", False)

        if requires_autocommit:
            # TimescaleDB continuous-aggregate DDL cannot run inside a transaction.
            # psycopg3 pool connections cannot have autocommit toggled after
            # checkout, so open a raw connection directly from the pool's conninfo.
            with _psycopg.connect(pool.conninfo, autocommit=True) as conn:
                if sql:
                    conn.execute(sql)
                if python_fn is not None:
                    python_fn(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (migration_id, description) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (migration["id"], migration["description"]),
                )
        else:
            with pool.connection() as conn:
                if sql:
                    conn.execute(sql)
                if python_fn is not None:
                    python_fn(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (migration_id, description) "
                    "VALUES (%s, %s)",
                    (migration["id"], migration["description"]),
                )
                conn.commit()
        _logger.info(
            "Applied migration: %s — %s", migration["id"], migration["description"]
        )
        newly_applied.append(migration["id"])

    return newly_applied


def list_migration_state(
    pool: ConnectionPool,
    migrations: list[dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    """Return applied/pending state for the given migration track.

    Returns::

        {
            "applied": [{"id": ..., "description": ..., "applied_at": ...}, ...],
            "pending": [{"id": ..., "description": ...}, ...],
        }

    If the ``schema_migrations`` table does not exist, all migrations are
    returned as pending and ``applied`` is empty.
    """
    if not _schema_migrations_table_exists(pool):
        return {
            "applied": [],
            "pending": [
                {"id": m["id"], "description": m["description"]} for m in migrations
            ],
        }

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT migration_id, description, applied_at "
                "FROM schema_migrations "
                "ORDER BY migration_id"
            )
            db_rows = cur.fetchall()
        conn.commit()

    applied_ids = {r["migration_id"] for r in db_rows}

    applied = [
        {
            "id": r["migration_id"],
            "description": r["description"] or "",
            "applied_at": r["applied_at"].isoformat() if r["applied_at"] else None,
        }
        for r in db_rows
    ]
    pending = [
        {"id": m["id"], "description": m["description"]}
        for m in migrations
        if m["id"] not in applied_ids
    ]

    return {"applied": applied, "pending": pending}
