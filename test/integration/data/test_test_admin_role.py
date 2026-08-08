"""The test-fixture admin credential must not reach production (913 D9).

``MT_TIMESCALE_TEST_URL`` is the admin credential the ephemeral-database
fixtures use. It genuinely needs ``CREATE DATABASE`` / ``DROP DATABASE``, which
the application role deliberately lacks — but that is *all* it needs.

It used to be ``postgres``. Since ``swap_dbname`` builds a URL for any database
on the same cluster, a fixture holding a superuser admin URL could reach
``trading`` by changing one path component; the only thing preventing that was
the convention that fixtures generate ``mt_test_*`` names. Convention, not
enforcement — the shape of the 2026-08-04 incident one layer down.

These tests assert the credential in use is genuinely limited. They fail if
someone repoints ``MT_TIMESCALE_TEST_URL`` back at a superuser.
"""

from __future__ import annotations

import os

import psycopg
import pytest

#: Databases the test tier must never be able to read. `trading` is production.
PRODUCTION_DB = "trading"

ADMIN_URL = os.environ.get("MT_TIMESCALE_TEST_URL", "")


@pytest.fixture
def admin_conn() -> psycopg.Connection:
    """Connection using the configured test-admin credential.

    Skips only when unconfigured. A connection failure propagates: it means the
    credential is wrong, which is a real problem, not a reason to pass silently.
    """
    if not ADMIN_URL:
        pytest.skip("MT_TIMESCALE_TEST_URL not set")
    with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=10) as conn:
        conn.execute("SET statement_timeout = '20s'")
        yield conn


def test_test_admin_is_not_a_superuser(admin_conn: psycopg.Connection) -> None:
    """A superuser here can read and destroy production regardless of grants."""
    row = admin_conn.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert row is not None
    is_super, bypass_rls = row
    assert not is_super, (
        "MT_TIMESCALE_TEST_URL is a superuser. The test tier only needs "
        "CREATEDB; a superuser credential can reach production by swapping the "
        "database name in the URL (913 D9). Repoint it at trading_test_admin."
    )
    assert not bypass_rls


def test_test_admin_can_still_create_databases(
    admin_conn: psycopg.Connection,
) -> None:
    """The one privilege the fixtures actually require."""
    row = admin_conn.execute(
        "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert row is not None and row[0], (
        "the test-admin credential cannot CREATE DATABASE; the ephemeral_db "
        "and migrated_db fixtures depend on it"
    )


def test_test_admin_cannot_read_production(admin_conn: psycopg.Connection) -> None:
    """The negative case this task exists to lock in.

    Reads are attempted against `trading` by name — the exact move a fixture
    would make via ``swap_dbname``. Read-only by construction: a SELECT that
    succeeds proves the problem; it does not cause one.
    """
    if admin_conn.info.dbname == PRODUCTION_DB:
        pytest.fail(
            f"MT_TIMESCALE_TEST_URL points directly at {PRODUCTION_DB!r}. "
            "The test tier must never be configured against production."
        )

    target = _swap_db(ADMIN_URL, PRODUCTION_DB)
    try:
        with psycopg.connect(target, autocommit=True, connect_timeout=10) as conn:
            conn.execute("SET statement_timeout = '10s'")
            conn.execute("SELECT 1 FROM instruments LIMIT 1")
    except psycopg.errors.InsufficientPrivilege:
        return  # connected, but cannot read — the intended state
    except psycopg.OperationalError:
        return  # cannot even connect — stricter still, also fine
    pytest.fail(
        f"the test-admin credential can READ {PRODUCTION_DB}.instruments. A "
        "fixture holding MT_TIMESCALE_TEST_URL could reach production by "
        "swapping the database name (913 D9)."
    )


def test_test_admin_holds_no_grants_on_production(
    admin_conn: psycopg.Connection,
) -> None:
    """Assert the absence structurally, not only behaviorally."""
    if admin_conn.info.dbname == PRODUCTION_DB:
        pytest.fail("MT_TIMESCALE_TEST_URL points at production")

    target = _swap_db(ADMIN_URL, PRODUCTION_DB)
    try:
        with psycopg.connect(target, autocommit=True, connect_timeout=10) as conn:
            conn.execute("SET statement_timeout = '10s'")
            rows = conn.execute(
                "SELECT count(*) FROM information_schema.table_privileges "
                "WHERE grantee = current_user"
            ).fetchone()
    except psycopg.OperationalError:
        return  # cannot connect at all; nothing to assert
    assert rows is not None and rows[0] == 0, (
        f"the test-admin credential holds {rows[0]} table grants on "
        f"{PRODUCTION_DB}; it should hold none"
    )


def _swap_db(url: str, dbname: str) -> str:
    """Return ``url`` repointed at ``dbname`` — the move under test."""
    from urllib.parse import urlparse, urlunparse

    return urlunparse(urlparse(url)._replace(path=f"/{dbname}"))
