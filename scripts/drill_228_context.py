"""Shared state and connections for the slice 228 restore drill.

``DrillContext`` carries what one step hands the next (the drill directory,
the production lock connection, step 2's segment). Connections:

- production ``17/tick``: the cluster row's maintenance URL from ``.env``,
  read-only, with TD6's statement timeout;
- the scratch server: its socket in the drill directory, as ``postgres``
  (``local all all trust``, reachable by manta only).

``rebuild_env`` is step 6's subprocess environment. It never edits ``.env``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
from backup_clusters import BackupCluster
from cutover_227_helpers import Step, StepFailed
from cutover_227_host import env_value
from drill_228_host import ArchiveSet
from drill_228_lifecycle import (
    PGDATA_NAME,
    PROD_STATEMENT_TIMEOUT,
    RESTORE_NAME,
    SCRATCH_STATEMENT_TIMEOUT,
    SOCKET_NAME,
    DrillLock,
)

from manta_trading.data.tick.constants import TICK_ARCHIVE_DIR_ENV
from manta_trading.market.schema.databases import Credential, Database, env_var_for

#: The restored production database and the rebuild's database (TD5).
TICK_DB = "trading_tick"
DRILL_DB = "trading_tick_drill"
#: The scratch server's superuser: the restored cluster's own.
SCRATCH_SUPERUSER = "postgres"
#: provision_tick_roles.sql's default role names (the restored cluster has them).
TICK_APP_ROLE = "tick_app"
TICK_MIGRATE_ROLE = "tick_migrate"
READ_ONLY = "-c default_transaction_read_only=on"
#: A URL query parameter carrying ``READ_ONLY`` (libpq ``options``).
READ_ONLY_URL_PARAM = f"options={quote(READ_ONLY)}"
SERVER_LOG = "server.log"


def _ms(span: timedelta) -> int:
    return int(span.total_seconds() * 1000)


@dataclass
class DrillContext:
    checkout: Path
    tick: BackupCluster
    archive: Path
    stamp: str
    lock: DrillLock | None = None
    drill: Path | None = None
    prod: psycopg.Connection[Any] | None = None
    prod_settings: dict[str, str] = field(default_factory=dict)
    live_set: ArchiveSet | None = None
    needed_segment: str = ""
    base: Path | None = None
    timings: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def env_file(self) -> Path:
        return self.checkout / ".env"

    @property
    def wal_dir(self) -> Path:
        return self.tick.backup_root / "wal"

    @property
    def base_dir(self) -> Path:
        return self.tick.backup_root / "base"

    def need_drill(self) -> Path:
        if self.drill is None:
            raise StepFailed("no drill directory yet (step 0 did not run)")
        return self.drill

    @property
    def pgdata(self) -> Path:
        return self.need_drill() / PGDATA_NAME

    @property
    def sockdir(self) -> Path:
        return self.need_drill() / SOCKET_NAME

    @property
    def server_log(self) -> Path:
        return self.need_drill() / SERVER_LOG

    @property
    def restored_archive(self) -> Path:
        """Where restic puts the archive: its absolute path under the target."""
        return self.need_drill() / RESTORE_NAME / self.archive.relative_to("/")

    def env(self, key: str) -> str:
        value = env_value(self.env_file, key)
        if not value:
            raise StepFailed(f"{key} is not set in {self.env_file}")
        return value


# --- Connections --------------------------------------------------------------


def connect_production(ctx: DrillContext) -> psycopg.Connection[Any]:
    """Production ``17/tick`` over the cluster row's URL: read-only, bounded."""
    options = f"{READ_ONLY} -c statement_timeout={_ms(PROD_STATEMENT_TIMEOUT)}"
    return psycopg.connect(ctx.env(ctx.tick.url_key), autocommit=True, options=options)


def scratch_url(ctx: DrillContext, db: str, user: str = SCRATCH_SUPERUSER) -> str:
    return f"postgresql://{user}@/{db}?host={quote(str(ctx.sockdir), safe='/')}"


def connect_scratch(ctx: DrillContext, db: str = TICK_DB) -> psycopg.Connection[Any]:
    options = f"{READ_ONLY} -c statement_timeout={_ms(SCRATCH_STATEMENT_TIMEOUT)}"
    return psycopg.connect(scratch_url(ctx, db), autocommit=True, options=options)


def read_only_url(url: str) -> str:
    """``url`` with ``default_transaction_read_only=on`` forced by libpq options."""
    if "options=" in url:
        raise StepFailed("the calendar URL already sets options; refusing to merge")
    return f"{url}{'&' if '?' in url else '?'}{READ_ONLY_URL_PARAM}"


def rebuild_env(ctx: DrillContext) -> dict[str, str]:
    """Step 6's subprocess environment (TD1 step 6).

    The tick URLs point at the drill database on the scratch socket and the
    archive at the restored copy. The calendar URL is ``.env``'s, forced
    read-only: ``PGOPTIONS`` can't do that, as it would also make the drill
    database read-only.
    """
    calendar_key = env_var_for(Database.PRIMARY, Credential.APPLICATION)
    return os.environ | {
        env_var_for(Database.TICK, Credential.APPLICATION): scratch_url(
            ctx, DRILL_DB, TICK_APP_ROLE
        ),
        env_var_for(Database.TICK, Credential.MAINTENANCE): scratch_url(
            ctx, DRILL_DB, TICK_MIGRATE_ROLE
        ),
        TICK_ARCHIVE_DIR_ENV: str(ctx.restored_archive),
        calendar_key: read_only_url(ctx.env(calendar_key)),
    }


# --- Recording ----------------------------------------------------------------


def check(step: Step, label: str, expected: object, seen: object) -> None:
    """Record ``label: expected X, seen Y``; a mismatch fails the step by name."""
    step.seen.append(f"{label}: expected {expected}, seen {seen}")
    if expected != seen:
        raise StepFailed(f"{label}: expected {expected}, seen {seen}")
