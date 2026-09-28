"""Database identity: which databases exist and which credential reaches each.

Slice 923. The primary ``trading`` database and the tick database each have an
application (DML) and a maintenance (DDL) credential. This module is the one
place those four settings fields are named; everything else asks for a
``(Database, Credential)`` pair.

It sits below the CLI deliberately: the API server and the pass modules will
resolve the tick application URL when they gain their second pool (slice 223
onward), and they must not import from ``cli``.

There is no fallback in any direction. Tick maintenance never falls back to
tick application, and tick never falls back to primary; an unset URL is an
error naming its variable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from manta_trading.config import Settings

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool


class Database(StrEnum):
    """A database the migration tooling can target."""

    PRIMARY = "primary"
    TICK = "tick"


class Credential(StrEnum):
    """Which role's credential a command connects with."""

    APPLICATION = "application"
    MAINTENANCE = "maintenance"


#: (database, credential) → ``Settings`` field holding its URL.
DATABASE_URL_FIELDS: dict[tuple[Database, Credential], str] = {
    (Database.PRIMARY, Credential.APPLICATION): "timescale_db_url",
    (Database.PRIMARY, Credential.MAINTENANCE): "timescale_maintenance_url",
    (Database.TICK, Credential.APPLICATION): "tick_db_url",
    (Database.TICK, Credential.MAINTENANCE): "tick_maintenance_url",
}


def env_var_for(database: Database, credential: Credential) -> str:
    """Return the environment variable that sets this pair's URL."""
    prefix = Settings.model_config["env_prefix"]
    return prefix + DATABASE_URL_FIELDS[(database, credential)].upper()


class DatabaseNotConfiguredError(Exception):
    """The URL for a (database, credential) pair is unset or empty.

    For a maintenance credential the message also names the application
    variable it will not fall back to; for the primary database that is,
    byte for byte, slice 913's ``_get_maintenance_url`` message.
    """

    def __init__(self, database: Database, credential: Credential) -> None:
        self.env_var = env_var_for(database, credential)
        reason = ""
        if credential is Credential.MAINTENANCE:
            app_var = env_var_for(database, Credential.APPLICATION)
            reason = (
                "This command performs schema or maintenance work and requires "
                f"the migration credential; it will not fall back to {app_var}. "
            )
        super().__init__(
            f"{self.env_var} not configured. {reason}"
            "Set the environment variable or add it to your .env file."
        )


def resolve_database_url(
    settings: Settings, database: Database, credential: Credential
) -> str:
    """Return the URL for ``(database, credential)``; never consult another field."""
    url = getattr(settings, DATABASE_URL_FIELDS[(database, credential)])
    if not url:
        raise DatabaseNotConfiguredError(database, credential)
    return url


#: How many foreign ids a misroute refusal names; the rest are counted.
_MISROUTE_IDS_SHOWN = 5


def _database_by_migration_id() -> dict[str, Database]:
    """Map every non-bootstrap migration id to the database its track targets."""
    # Function-local: ``migrations`` imports ``Database`` from this module.
    from manta_trading.market.schema.migrations import TRACK_REGISTRY
    from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID

    return {
        m["id"]: spec.database
        for spec in TRACK_REGISTRY.values()
        for m in spec.migrations
        if m["id"] != BOOTSTRAP_MIGRATION_ID
    }


class LedgerMisrouteError(Exception):
    """The target database's ledger holds another database's migrations."""

    def __init__(
        self, database: Database, track: str, foreign_ids: list[str], env_var: str
    ) -> None:
        self.database = database
        self.track = track
        self.foreign_ids = foreign_ids
        self.env_var = env_var
        owners = _database_by_migration_id()
        routed_to = ", ".join(sorted({owners[i] for i in foreign_ids}))
        shown = ", ".join(foreign_ids[:_MISROUTE_IDS_SHOWN])
        more = len(foreign_ids) - _MISROUTE_IDS_SHOWN
        if more > 0:
            shown += f", … {more} more"
        super().__init__(
            f"refusing: this database's ledger holds migrations of track(s) "
            f"routed to {routed_to} ({shown}); check {env_var} "
            f"(track '{track}' targets the {database} database)"
        )


def foreign_ledger_ids(ledger_ids: list[str], database: Database) -> list[str]:
    """Return the ledger ids whose tracks target a database other than ``database``.

    The shared bootstrap id and ids in no current track are ignored: a
    production ledger can hold retired ids, and they must not trip the guard.
    """
    owners = _database_by_migration_id()
    return sorted(i for i in ledger_ids if i in owners and owners[i] is not database)


def assert_ledger_belongs(
    pool: ConnectionPool, database: Database, track: str, env_var: str
) -> None:
    """Refuse to migrate a database whose ledger belongs to another (D5).

    Checks the ledger rather than comparing URLs, because host aliases defeat
    URL comparison. A missing ledger is a bare database and passes. Issues
    only ``SELECT``s, and runs before any DDL.
    """
    with pool.connection() as conn:
        row = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()
        if row is None or row[0] is None:
            return
        rows = conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
    foreign = foreign_ledger_ids([r[0] for r in rows], database)
    if foreign:
        raise LedgerMisrouteError(database, track, foreign, env_var)
