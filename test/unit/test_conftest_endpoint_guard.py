"""The test-database guard must refuse production and must never skip silently.

Two failure modes this project has actually suffered:

* a database tier that *skipped* because its variable was unset, so a green run
  proved nothing;
* a test fixture receiving the production URL, which in 2026-08 destroyed six
  production tables.

The guard in ``conftest`` closes both. These tests pin its decision logic, which
is a pure function so it can be checked without a live database.
"""

from __future__ import annotations

from conftest import endpoint_of, points_at_production

PROD = "192.168.1.144:5432"


def test_endpoint_ignores_credentials_and_database() -> None:
    """The endpoint is what identifies a server, not the rest of the URL."""
    assert endpoint_of("postgresql://u:p@192.168.1.144:5432/trading") == PROD
    assert endpoint_of("postgresql://other:x@192.168.1.144:5432/postgres") == PROD


def test_endpoint_defaults_the_port() -> None:
    """A URL without an explicit port still names the standard one."""
    assert endpoint_of("postgresql://u:p@192.168.1.144/trading") == PROD


def test_endpoint_of_nothing_is_none() -> None:
    assert endpoint_of("") is None
    assert endpoint_of("not-a-url") is None


def test_production_is_refused_however_it_is_dressed_up() -> None:
    """Matching is semantic — a different password or database is still production."""
    assert points_at_production("postgresql://u:p@192.168.1.144:5432/postgres", PROD)
    assert points_at_production("postgresql://z:q@192.168.1.144:5432/trading", PROD)
    assert points_at_production("postgresql://u:p@192.168.1.144/postgres", PROD)


def test_the_dedicated_test_host_is_allowed() -> None:
    assert not points_at_production(
        "postgresql://u:p@192.168.1.143:5432/postgres", PROD
    )


def test_a_different_port_on_the_same_host_is_a_different_server() -> None:
    """A second cluster on the production machine is not production."""
    assert not points_at_production(
        "postgresql://u:p@192.168.1.144:5433/postgres", PROD
    )


def test_unknown_production_endpoint_does_not_block() -> None:
    """A documented limit: with nothing to compare against, the guard stands down.

    It protects a misconfigured ``.env`` on a machine that also has production
    configured — the case that has actually occurred — and cannot protect a
    machine that never knew production's address.
    """
    assert not points_at_production("postgresql://u:p@192.168.1.144:5432/x", None)
