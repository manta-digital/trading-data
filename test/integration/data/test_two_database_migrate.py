"""Track routing against two real databases (slice 923, Functional Requirements 1–6).

The CLI runs for real (``CliRunner``) with ``Settings(_env_file=None)`` whose
primary pair names a throwaway primary database and whose tick pair names a
throwaway tick database, both created by fixtures on the test cluster.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from psycopg_pool import ConnectionPool
from tick_support.database import two_database_settings
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.market.schema import migrations as migrations_pkg
from manta_trading.market.schema.databases import Database
from manta_trading.market.schema.migrations import TRACKS, TrackSpec
from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID

runner = CliRunner()

#: A port nothing listens on at the test host (tcpmux, never served).
UNUSED_PORT = 1

#: Replaces the pool's 30 s getconn wait so the unreachable case is quick.
SHORT_POOL_TIMEOUT_S = 2.0


def _invoke(settings: Settings, *args: str) -> Any:
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        return runner.invoke(app, ["data", *args])


def _flat(output: str) -> str:
    """Undo rich's wrapping at the test terminal's 80 columns."""
    return " ".join(output.split())


def _ledger(url: str) -> list[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT migration_id FROM schema_migrations ORDER BY 1"
        ).fetchall()
    return [r[0] for r in rows]


def _ledger_exists(url: str) -> bool:
    with psycopg.connect(url) as conn:
        row = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()
    return row is not None and row[0] is not None


def test_apply_tick_writes_only_the_tick_ledger(
    migrated_db: str, ephemeral_tick_db: str
) -> None:
    """Functional Requirement 1."""
    primary_before = _ledger(migrated_db)
    settings = two_database_settings(migrated_db, ephemeral_tick_db)

    result = _invoke(settings, "migrate", "apply", "--track", "tick")

    assert result.exit_code == 0, result.output
    assert _ledger(ephemeral_tick_db) == [BOOTSTRAP_MIGRATION_ID]
    assert _ledger(migrated_db) == primary_before


def test_status_tick_reads_the_tick_ledger(
    migrated_db: str, migrated_tick_db: str
) -> None:
    """Functional Requirement 2: status resolves the tick application URL."""
    settings = two_database_settings(migrated_db, migrated_tick_db)

    result = _invoke(settings, "migrate", "status", "--track", "tick", "--json")

    assert result.exit_code == 0, result.output
    state = json.loads(result.output)
    assert [e["id"] for e in state["applied"]] == [BOOTSTRAP_MIGRATION_ID]
    assert state["pending"] == []


def test_tick_apply_refuses_a_primary_database(migrated_db: str) -> None:
    """Functional Requirement 4: tick maintenance URL aimed at the primary."""
    before = _ledger(migrated_db)
    settings = two_database_settings(migrated_db, migrated_db)

    result = _invoke(settings, "migrate", "apply", "--track", "tick")

    assert result.exit_code == 1
    output = _flat(result.output)
    assert "refusing:" in output
    assert "routed to primary" in output
    assert TRACKS["minute"][1]["id"] in output
    assert "check MT_TICK_MAINTENANCE_URL" in output
    assert _ledger(migrated_db) == before


@pytest.fixture
def registry_with_tick_id(monkeypatch: pytest.MonkeyPatch) -> str:
    """Give the tick track one real id, as slice 222 will."""
    fake_id = "tick_001_fake"
    registry = dict(migrations_pkg.TRACK_REGISTRY)
    registry["tick"] = TrackSpec(
        Database.TICK,
        [*TRACKS["tick"], {"id": fake_id, "description": "x", "sql": "SELECT 1"}],
    )
    monkeypatch.setattr(migrations_pkg, "TRACK_REGISTRY", registry)
    return fake_id


def test_primary_apply_refuses_a_tick_ledger(
    migrated_db: str, ephemeral_tick_db: str, registry_with_tick_id: str
) -> None:
    """Functional Requirement 4, reverse: a primary apply into a tick-marked ledger."""
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (migration_id) VALUES (%s)",
            (registry_with_tick_id,),
        )
    before = _ledger(migrated_db)
    settings = two_database_settings(migrated_db, ephemeral_tick_db)

    result = _invoke(settings, "migrate", "apply", "--track", "minute")

    assert result.exit_code == 1
    output = _flat(result.output)
    assert f"routed to tick ({registry_with_tick_id})" in output
    assert "check MT_TIMESCALE_MAINTENANCE_URL" in output
    assert _ledger(migrated_db) == before


def test_init_tick_brings_a_bare_database_to_head(
    migrated_db: str, ephemeral_tick_db: str
) -> None:
    """Functional Requirement 5, tick side."""
    assert not _ledger_exists(ephemeral_tick_db)
    settings = two_database_settings(migrated_db, ephemeral_tick_db)

    result = _invoke(settings, "init", "--database", "tick", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["pending_remaining"] == 0
    assert payload["applied_total"] == len(TRACKS["tick"])
    assert _ledger(ephemeral_tick_db) == sorted(m["id"] for m in TRACKS["tick"])


def test_init_without_option_still_reports_the_minute_track(
    migrated_db: str, ephemeral_tick_db: str
) -> None:
    """Functional Requirement 5, primary side: the default is unchanged."""
    settings = two_database_settings(migrated_db, ephemeral_tick_db)

    result = _invoke(settings, "init", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied_total"] == len(TRACKS["minute"])
    assert payload["pending_remaining"] == 0
    assert not _ledger_exists(ephemeral_tick_db)


@contextmanager
def _short_pool_timeout() -> Iterator[None]:
    """Shorten the DB wrapper's getconn wait in this test only."""
    short = functools.partial(ConnectionPool, timeout=SHORT_POOL_TIMEOUT_S)
    with patch("manta_trading.market.timescale_minute_db.ConnectionPool", short):
        yield


def test_unreachable_tick_database_is_one_line(
    migrated_db: str, ephemeral_tick_db: str
) -> None:
    """Functional Requirement 6: no traceback, the variable named, nothing applied."""
    parsed = urlparse(ephemeral_tick_db)
    # connect_timeout lets the pool's workers give up promptly, so closing the
    # pool does not wait on a host that drops packets instead of refusing.
    unreachable = urlunparse(
        parsed._replace(
            netloc=f"{parsed.username}@{parsed.hostname}:{UNUSED_PORT}",
            query="connect_timeout=1",
        )
    )
    settings = two_database_settings(migrated_db, ephemeral_tick_db)
    settings.tick_maintenance_url = unreachable

    with _short_pool_timeout():
        result = _invoke(settings, "migrate", "apply", "--track", "tick")

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert result.output.count("Error:") == 1, result.output
    assert _flat(result.output).startswith(
        "Error: could not connect to the tick database (MT_TICK_MAINTENANCE_URL): "
    )
    assert not _ledger_exists(ephemeral_tick_db)
