"""Tick-database test support (slice 923): the provisioning artifact, applied for real.

Lives outside ``conftest.py`` so test modules can import ``ProvisionedTickDb``
without importing a ``conftest`` by name (the unit tier already imports
``test/conftest.py`` as ``conftest``). Every destructive statement here targets
a database or role the caller's fixture created.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pytest

PROVISION_TICK_SQL = (
    Path(__file__).resolve().parents[2] / "scripts" / "provision_tick_roles.sql"
)


@dataclass(frozen=True)
class ProvisionedTickDb:
    """A tick database and roles created by ``provision_tick_roles.sql``."""

    name: str
    #: Excluded from ``repr``: pytest prints fixture values in failure headers.
    url: str = field(repr=False)
    app_role: str
    migrate_role: str

    def apply_artifact(self, admin_url: str) -> subprocess.CompletedProcess[str]:
        """Run the artifact with this database's names; the caller checks the exit."""
        return subprocess.run(
            [
                "psql",
                admin_url,
                "-q",
                "-v",
                f"tick_db={self.name}",
                "-v",
                f"app_role={self.app_role}",
                "-v",
                f"migrate_role={self.migrate_role}",
                "-f",
                str(PROVISION_TICK_SQL),
            ],
            capture_output=True,
            text=True,
        )


def apply_artifact_or_fail(tick: ProvisionedTickDb, admin_url: str) -> None:
    result = tick.apply_artifact(admin_url)
    if result.returncode != 0:
        pytest.fail(
            f"provision_tick_roles.sql failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )


def apply_tick_track_as(url: str, role: str) -> None:
    """Apply ``TRACKS["tick"]`` with every statement run as ``role``."""
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations import TRACKS
    from manta_trading.market.schema.runner import apply_migrations

    def _as_role(conn: psycopg.Connection[Any]) -> None:
        conn.autocommit = True
        conn.execute(f'SET ROLE "{role}"')
        conn.autocommit = False

    with ConnectionPool(url, min_size=1, max_size=1, configure=_as_role) as pool:
        apply_migrations(pool, TRACKS["tick"])


def drop_tick_db(tick: ProvisionedTickDb, admin_url: str) -> None:
    """Drop only what this fixture created: the database, then both roles."""
    with psycopg.connect(admin_url, autocommit=True) as admin:
        # Non-superuser backends only; see ``ephemeral_db``'s teardown.
        admin.execute(
            "SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a "
            "JOIN pg_roles r ON r.rolname = a.usename "
            "WHERE a.datname = %s AND a.pid <> pg_backend_pid() "
            "AND NOT r.rolsuper",
            (tick.name,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{tick.name}"')
        # 913's order: DROP OWNED, then revoke the fixture's membership, then
        # DROP ROLE (each step removes a dependency that blocks the next).
        for role in (tick.app_role, tick.migrate_role):
            exists = admin.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone()
            if exists is None:  # the artifact failed before creating it
                continue
            # DROP OWNED needs the role's privileges. The fixture may have
            # failed before granting its membership, so grant it here (the
            # creator holds ADMIN on the role; re-granting is a no-op).
            admin.execute(f'GRANT "{role}" TO CURRENT_USER')
            admin.execute(f'DROP OWNED BY "{role}" CASCADE')
            admin.execute(f'REVOKE "{role}" FROM CURRENT_USER')
            admin.execute(f'DROP ROLE IF EXISTS "{role}"')
