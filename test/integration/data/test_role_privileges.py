"""Assert the least-privilege role split holds (slice 913).

These are the tests that make the protection non-regressible. On 2026-08-04 a
test fixture received the production URL and ran ``TRUNCATE ... CASCADE``
against six metadata tables. Every control added since is procedural — it stops
a class of caller from *obtaining* the URL without limiting what the URL can
*do*. The role split closes that: under the application role the same statement
dies on ``permission denied``.

**Everything here runs against an ephemeral database the fixture created.**
An earlier revision targeted production and had to be abandoned: ``DROP TABLE``
requires an ``ACCESS EXCLUSIVE`` lock, so even inside a rolled-back transaction
it queues behind live readers and then blocks every reader and writer of that
table. Rolling back protects data; it does not protect availability. It also
violated the ``sql.md`` rule this slice exists to enforce — a fixture issuing
TRUNCATE/DROP may only target a database it created.

Roles are cluster-wide (``pg_authid`` is a shared catalog) while table grants
are per-database, and the ephemeral database lives on the same cluster as
production. The fixture therefore provisions **uniquely named** throwaway roles
and drops them on teardown; reusing ``trading_app`` would mutate the role
production depends on.
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

PROVISION_SQL = (
    Path(__file__).resolve().parents[3] / "scripts" / "provision_roles.sql"
)

#: Tables the application role must be able to write. Mirrors the enumerated
#: grant list in the provisioning artifact (D3).
WRITE_TABLES = (
    "minute_ohlcv",
    "daily_ohlcv",
    "data_gaps",
    "acquisition_state",
    "daemon_heartbeat",
    "trading_sessions",
    "instruments",
    "provider_symbol_mapping",
    "universe_members",
    "splits",
    "dividends",
)

LEDGER = "schema_migrations"


@pytest.fixture
def provisioned_roles(migrated_db: str) -> Iterator[tuple[str, str]]:
    """Apply the real provisioning artifact to an ephemeral migrated database.

    Yields ``(db_url, app_role)``. The artifact under test is
    ``scripts/provision_roles.sql`` itself — not a re-implementation — so a
    defect in the file the production run applies is a test failure here.

    Skips only when the database is *not configured* (via ``migrated_db``).
    Provisioning failures deliberately propagate: a broad except-to-skip would
    turn this entire suite green while asserting nothing, which is the one
    outcome this slice cannot tolerate.
    """
    suffix = uuid.uuid4().hex[:10]
    app_role = f"t913_app_{suffix}"
    migrate_role = f"t913_mig_{suffix}"

    result = subprocess.run(
        [
            "psql",
            migrated_db,
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-v",
            f"app_role={app_role}",
            "-v",
            f"migrate_role={migrate_role}",
            "-f",
            str(PROVISION_SQL),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"provision_roles.sql failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )

    try:
        yield migrated_db, app_role
    finally:
        # DROP ROLE fails while the role holds grants or owns objects.
        admin_url = urlunparse(urlparse(migrated_db)._replace(path="/postgres"))
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            for role in (app_role, migrate_role):
                conn.execute(f'DROP OWNED BY "{role}" CASCADE')
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for role in (app_role, migrate_role):
                admin.execute(f'DROP ROLE IF EXISTS "{role}"')


@pytest.fixture
def app_conn(
    provisioned_roles: tuple[str, str],
) -> Iterator[psycopg.Connection]:
    """Connection to the ephemeral database with the session role set to the app role.

    ``SET ROLE`` is authorized against ``session_user``, so it succeeds only
    while the connecting credential is a superuser or a member of the target
    role. A failure here must propagate rather than skip — see the module
    docstring.

    ``lock_timeout`` is set so that a future change reintroducing lock
    contention fails fast instead of hanging the suite, which is how the
    production-targeting revision of these tests was discovered.
    """
    db_url, app_role = provisioned_roles
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("SET statement_timeout = '30s'")
        conn.execute("SET lock_timeout = '5s'")
        conn.execute(f'SET ROLE "{app_role}"')
        yield conn


def _assert_denied(conn: psycopg.Connection, statement: str) -> None:
    """Run ``statement`` in a transaction that is always rolled back.

    Fails if the statement was permitted. The rollback means even a regression
    that grants the privilege cannot destroy the fixture's data while proving
    it.
    """
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

    pytest.fail(
        f"{statement!r} was PERMITTED for the application role. The "
        "2026-08-04 incident is reachable again; check "
        "scripts/provision_roles.sql."
    )


def test_session_role_is_the_application_role(
    app_conn: psycopg.Connection, provisioned_roles: tuple[str, str]
) -> None:
    """Guard the guard: prove the fixture actually switched roles.

    Without this, every denial below would pass trivially if ``SET ROLE`` had
    silently not taken effect.
    """
    _, app_role = provisioned_roles
    row = app_conn.execute("SELECT current_user").fetchone()
    assert row is not None and row[0] == app_role


def test_truncate_is_denied(app_conn: psycopg.Connection) -> None:
    """The exact statement shape that destroyed production on 2026-08-04."""
    _assert_denied(app_conn, "TRUNCATE instruments")


def test_drop_table_is_denied(app_conn: psycopg.Connection) -> None:
    """Safe to assert here only because the target is a fixture-created database."""
    _assert_denied(app_conn, "DROP TABLE daemon_heartbeat")


def test_ledger_delete_is_denied(app_conn: psycopg.Connection) -> None:
    """The migration ledger is readable but never writable by the app role."""
    _assert_denied(app_conn, f"DELETE FROM {LEDGER}")


def test_ledger_is_readable(app_conn: psycopg.Connection) -> None:
    """SELECT-only means SELECT must still work — migrate status depends on it."""
    row = app_conn.execute(f"SELECT count(*) FROM {LEDGER}").fetchone()
    assert row is not None and row[0] > 0


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_write_tables_are_readable(app_conn: psycopg.Connection, table: str) -> None:
    app_conn.execute(f"SELECT 1 FROM {table} LIMIT 1")


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_write_tables_grant_dml(app_conn: psycopg.Connection, table: str) -> None:
    """Assert UPDATE privilege without depending on any table's columns.

    PostgreSQL checks privileges before column validity, so a role holding
    UPDATE fails on ``cannot assign to system column "ctid"`` while a role
    without it fails on ``permission denied``. That distinction is the
    assertion.
    """
    try:
        app_conn.execute("BEGIN")
        app_conn.execute(f"UPDATE {table} SET ctid = ctid WHERE false")
    except psycopg.errors.InsufficientPrivilege as exc:
        pytest.fail(f"application role lacks UPDATE on {table}: {exc}")
    except psycopg.errors.Error as exc:
        assert "ctid" in str(exc), f"unexpected failure on {table}: {exc}"
    finally:
        app_conn.execute("ROLLBACK")


def test_continuous_aggregates_are_readable(app_conn: psycopg.Connection) -> None:
    """Caggs need explicit grants; GRANT ... ON ALL TABLES does not cover views.

    Asserted against the aggregates the migration chain actually materialized
    rather than production's list of 9 — a hardcoded count would be brittle
    across databases.
    """
    caggs = [
        row[0]
        for row in app_conn.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates "
            "ORDER BY view_name"
        ).fetchall()
    ]
    assert caggs, "migrated database materialized no continuous aggregates"

    for view in caggs:
        app_conn.execute(f"SELECT 1 FROM {view} LIMIT 1")


def test_temp_table_creation_is_permitted(app_conn: psycopg.Connection) -> None:
    """The COPY bulk-write hot path stages through a temp table (D2).

    Without TEMPORARY on the database, all minute ingestion fails.
    """
    app_conn.execute("BEGIN")
    try:
        app_conn.execute("CREATE TEMP TABLE _probe_913 (a int) ON COMMIT DROP")
    finally:
        app_conn.execute("ROLLBACK")


def test_truncate_privilege_is_absent_from_catalog(
    app_conn: psycopg.Connection, provisioned_roles: tuple[str, str]
) -> None:
    """Assert the absence structurally, not just behaviorally.

    A behavioral denial could mask a grant that exists but is shadowed. This
    checks the catalog directly: no TRUNCATE privilege on any application table.
    """
    _, app_role = provisioned_roles
    rows = app_conn.execute(
        "SELECT table_name FROM information_schema.table_privileges "
        "WHERE grantee = %s AND privilege_type = 'TRUNCATE'",
        (app_role,),
    ).fetchall()
    assert rows == [], f"application role holds TRUNCATE on: {[r[0] for r in rows]}"
