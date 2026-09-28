"""The tick database's roles and isolation hold (slice 923, Functional Requirement 8).

Runs ``scripts/provision_tick_roles.sql`` itself, via ``provisioned_tick_db``,
against a database that fixture created on the test cluster, with per-run role
names. Denied statements run only there, inside transactions that are rolled
back. The primary side is checked by reading the catalog, never by attempting
a statement (913 D8).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from tick_support.database import ProvisionedTickDb

LEDGER = "schema_migrations"


@pytest.fixture
def tick_app_conn(
    provisioned_tick_db: ProvisionedTickDb,
) -> Iterator[psycopg.Connection[Any]]:
    """Connection to the tick database with the session role set to the app role."""
    with psycopg.connect(provisioned_tick_db.url, autocommit=True) as conn:
        conn.execute("SET statement_timeout = '30s'")
        conn.execute("SET lock_timeout = '5s'")
        conn.execute(f'SET ROLE "{provisioned_tick_db.app_role}"')
        yield conn


def _assert_denied(conn: psycopg.Connection[Any], statement: str) -> None:
    """Fail if ``statement`` is permitted; always roll back (913 suite's pattern)."""
    try:
        conn.execute("BEGIN")
        conn.execute(statement)
    except psycopg.errors.InsufficientPrivilege:
        return
    except psycopg.errors.Error as exc:
        # DROP by a non-owner raises `must be owner of table ...`, a different
        # SQLSTATE than InsufficientPrivilege.
        assert "must be owner" in str(exc), (
            f"{statement!r} failed, but not on privilege: {exc}"
        )
        return
    finally:
        conn.execute("ROLLBACK")
    pytest.fail(f"{statement!r} was PERMITTED for the tick application role")


def test_session_role_is_the_application_role(
    tick_app_conn: psycopg.Connection[Any], provisioned_tick_db: ProvisionedTickDb
) -> None:
    """Guard the guard: every denial below would pass if SET ROLE had not taken."""
    row = tick_app_conn.execute("SELECT current_user").fetchone()
    assert row is not None and row[0] == provisioned_tick_db.app_role


@pytest.mark.parametrize(
    "statement",
    [
        f"TRUNCATE {LEDGER}",
        f"DROP TABLE {LEDGER}",
        f"INSERT INTO {LEDGER} (migration_id) VALUES ('tick_999_forged')",
        f"DELETE FROM {LEDGER}",
        f"UPDATE {LEDGER} SET description = 'x'",
    ],
)
def test_application_role_cannot_damage_the_ledger(
    tick_app_conn: psycopg.Connection[Any], statement: str
) -> None:
    _assert_denied(tick_app_conn, statement)


def test_application_role_reads_the_ledger(
    tick_app_conn: psycopg.Connection[Any],
) -> None:
    row = tick_app_conn.execute(f"SELECT count(*) FROM {LEDGER}").fetchone()
    assert row is not None and row[0] >= 1


def test_application_role_creates_temp_tables(
    tick_app_conn: psycopg.Connection[Any],
) -> None:
    """TEMPORARY is granted for slice 224's COPY staging."""
    tick_app_conn.execute("BEGIN")
    try:
        tick_app_conn.execute("CREATE TEMP TABLE staging_probe (x int) ON COMMIT DROP")
    finally:
        tick_app_conn.execute("ROLLBACK")


def test_migrate_role_owns_database_and_installs_timescaledb(
    provisioned_tick_db: ProvisionedTickDb,
) -> None:
    """D4: ownership, not membership in ``postgres``, is what permits the extension."""
    tick = provisioned_tick_db
    with psycopg.connect(tick.url, autocommit=True) as conn:
        owner = conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database "
            "WHERE datname = current_database()"
        ).fetchone()
        assert owner is not None and owner[0] == tick.migrate_role
        conn.execute(f'SET ROLE "{tick.migrate_role}"')
        conn.execute("CREATE EXTENSION timescaledb")
        try:
            ext = conn.execute(
                "SELECT pg_get_userbyid(extowner) FROM pg_extension "
                "WHERE extname = 'timescaledb'"
            ).fetchone()
            assert ext is not None and ext[0] == tick.migrate_role
        finally:
            conn.execute("DROP EXTENSION timescaledb")


def test_migrate_role_has_no_membership_in_postgres(
    provisioned_tick_db: ProvisionedTickDb,
) -> None:
    with psycopg.connect(provisioned_tick_db.url) as conn:
        row = conn.execute(
            "SELECT pg_has_role(%s, 'postgres', 'MEMBER')",
            (provisioned_tick_db.migrate_role,),
        ).fetchone()
    assert row is not None and row[0] is False


@pytest.fixture
def unrelated_app_role(test_admin_url: str) -> Iterator[str]:
    """Stands in for the primary's application role; created and dropped here."""
    role = f"t923_papp_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(test_admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE ROLE "{role}" LOGIN')
    try:
        yield role
    finally:
        with psycopg.connect(test_admin_url, autocommit=True) as admin:
            admin.execute(f'DROP ROLE IF EXISTS "{role}"')


def test_other_roles_cannot_connect_to_the_tick_database(
    provisioned_tick_db: ProvisionedTickDb, test_admin_url: str, unrelated_app_role: str
) -> None:
    """PUBLIC's CONNECT is revoked, so a primary role reaches nothing here."""
    with psycopg.connect(test_admin_url) as conn:
        row = conn.execute(
            "SELECT has_database_privilege(%s, %s, 'CONNECT')",
            (unrelated_app_role, provisioned_tick_db.name),
        ).fetchone()
    assert row is not None and row[0] is False


def _explicit_grants(
    conn: psycopg.Connection[Any], roles: tuple[str, ...]
) -> list[Any]:
    """ACL entries, owned relations and default ACLs naming ``roles`` here."""
    return conn.execute(
        """
        WITH r AS (SELECT oid FROM pg_roles WHERE rolname = ANY(%(roles)s))
        SELECT 'database', datname FROM pg_database, aclexplode(datacl) a
          WHERE datname = current_database() AND a.grantee IN (SELECT oid FROM r)
        UNION ALL
        SELECT 'schema', nspname FROM pg_namespace, aclexplode(nspacl) a
          WHERE a.grantee IN (SELECT oid FROM r)
        UNION ALL
        SELECT 'relation', relname FROM pg_class, aclexplode(relacl) a
          WHERE a.grantee IN (SELECT oid FROM r)
        UNION ALL
        SELECT 'owned', relname FROM pg_class WHERE relowner IN (SELECT oid FROM r)
        UNION ALL
        SELECT 'default acl', defaclobjtype::text FROM pg_default_acl
          WHERE defaclrole IN (SELECT oid FROM r)
             OR EXISTS (SELECT 1 FROM aclexplode(defaclacl) a
                        WHERE a.grantee IN (SELECT oid FROM r))
        """,
        {"roles": list(roles)},
    ).fetchall()


def test_tick_roles_have_nothing_in_the_primary_database(
    provisioned_tick_db: ProvisionedTickDb, session_migrated_db: str
) -> None:
    """Read from the catalog of a migrated primary throwaway database (913 D8)."""
    roles = (provisioned_tick_db.app_role, provisioned_tick_db.migrate_role)
    with psycopg.connect(session_migrated_db) as conn:
        assert _explicit_grants(conn, roles) == []


def _catalog_snapshot(tick: ProvisionedTickDb) -> dict[str, Any]:
    roles = [tick.app_role, tick.migrate_role]
    with psycopg.connect(tick.url) as conn:
        return {
            "database": conn.execute(
                "SELECT datdba::regrole::text, datacl::text FROM pg_database "
                "WHERE datname = current_database()"
            ).fetchall(),
            "ledger": conn.execute(
                "SELECT relowner::regrole::text, relacl::text FROM pg_class "
                "WHERE relname = %s",
                (LEDGER,),
            ).fetchall(),
            "public schema": conn.execute(
                "SELECT nspacl::text FROM pg_namespace WHERE nspname = 'public'"
            ).fetchall(),
            "default acl": conn.execute(
                "SELECT defaclrole::regrole::text, defaclobjtype, defaclacl::text "
                "FROM pg_default_acl ORDER BY 1, 2"
            ).fetchall(),
            "roles": conn.execute(
                "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin "
                "FROM pg_roles WHERE rolname = ANY(%s) ORDER BY 1",
                (roles,),
            ).fetchall(),
            "memberships": conn.execute(
                "SELECT roleid::regrole::text, member::regrole::text, set_option "
                "FROM pg_auth_members WHERE roleid::regrole::text = ANY(%s) "
                "OR member::regrole::text = ANY(%s) ORDER BY 1, 2",
                (roles, roles),
            ).fetchall(),
        }


def test_reapplying_the_artifact_changes_nothing(
    provisioned_tick_db: ProvisionedTickDb, test_admin_url: str
) -> None:
    before = _catalog_snapshot(provisioned_tick_db)
    result = provisioned_tick_db.apply_artifact(test_admin_url)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _catalog_snapshot(provisioned_tick_db) == before
