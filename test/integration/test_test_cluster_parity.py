"""The test cluster must run the same PostgreSQL and TimescaleDB as production.

Slice 917 moved the throwaway test databases onto a dedicated machine. When both
lived on one host, version parity was automatic; once they do not, it is a
property somebody has to maintain. Two mechanisms hold it: ``apt-mark hold`` on
the test host's packages, and this assertion.

The hold makes drift require a deliberate act. This test makes a deliberate act
visible, so a drifted cluster announces itself instead of quietly producing
different results from production.

**When production upgrades**, update the two constants below and re-pin the test
host per project-documents/user/runbooks/test-database-cluster.md. Changing them
here without doing the runbook step will fail this test, which is the intent.
"""

from __future__ import annotations

import re

import psycopg
import pytest

EXPECTED_POSTGRES_VERSION = "17.11"
EXPECTED_TIMESCALEDB_VERSION = "2.29.1"


@pytest.fixture(scope="module")
def cluster_versions(test_admin_url: str) -> tuple[str, str]:
    """Return the test cluster's ``(postgres, timescaledb)`` versions."""
    with psycopg.connect(test_admin_url, autocommit=True) as conn:
        raw = conn.execute("SELECT version()").fetchone()
        assert raw is not None
        match = re.search(r"PostgreSQL (\d+\.\d+)", raw[0])
        assert match, f"could not parse a version out of {raw[0]!r}"
        pg = match.group(1)

        row = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
        ).fetchone()
        ts = row[0] if row else ""
    return pg, ts


def test_postgres_matches_production(cluster_versions: tuple[str, str]) -> None:
    pg, _ = cluster_versions
    assert pg == EXPECTED_POSTGRES_VERSION, (
        f"test cluster runs PostgreSQL {pg}, production runs "
        f"{EXPECTED_POSTGRES_VERSION}. Migrations verified here no longer prove "
        "anything about production. Re-pin the test host, or update this "
        "constant if production is the one that moved."
    )


def test_timescaledb_matches_production(cluster_versions: tuple[str, str]) -> None:
    _, ts = cluster_versions
    assert ts, (
        "the timescaledb extension is not installed on the test cluster; "
        "migrations create hypertables and continuous aggregates and will fail"
    )
    assert ts == EXPECTED_TIMESCALEDB_VERSION, (
        f"test cluster runs TimescaleDB {ts}, production runs "
        f"{EXPECTED_TIMESCALEDB_VERSION}. Re-pin the test host, or update this "
        "constant if production is the one that moved."
    )


def test_background_workers_are_available(test_admin_url: str) -> None:
    """Zero workers turns policy tests into hangs rather than clear failures."""
    with psycopg.connect(test_admin_url, autocommit=True) as conn:
        row = conn.execute("SHOW timescaledb.max_background_workers").fetchone()
    assert row is not None
    assert int(row[0]) > 0, (
        "the test cluster has no TimescaleDB background workers, so any test "
        "waiting on a refresh policy will hang instead of failing usefully"
    )
