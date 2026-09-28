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

from manta_trading.config import Settings


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
    """The URL for a (database, credential) pair is unset or empty."""

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"{env_var} not configured. "
            "Set the environment variable or add it to your .env file."
        )


def resolve_database_url(
    settings: Settings, database: Database, credential: Credential
) -> str:
    """Return the URL for ``(database, credential)``; never consult another field."""
    url = getattr(settings, DATABASE_URL_FIELDS[(database, credential)])
    if not url:
        raise DatabaseNotConfiguredError(env_var_for(database, credential))
    return url
