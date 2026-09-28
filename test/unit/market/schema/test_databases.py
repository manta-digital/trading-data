"""Database identity and URL resolution (slice 923 D2).

Every ``Settings`` here is built with ``_env_file=None`` so a developer's
``.env`` cannot leak a URL into a no-fallback assertion.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from manta_trading.config import Settings
from manta_trading.market.schema import migrations as migrations_pkg
from manta_trading.market.schema.databases import (
    DATABASE_URL_FIELDS,
    Credential,
    Database,
    DatabaseNotConfiguredError,
    LedgerMisrouteError,
    assert_ledger_belongs,
    env_var_for,
    foreign_ledger_ids,
    resolve_database_url,
)
from manta_trading.market.schema.migrations import TRACKS, TrackSpec
from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID

_ALL_URL_VARS = (
    "MT_TIMESCALE_DB_URL",
    "MT_TIMESCALE_MAINTENANCE_URL",
    "MT_TICK_DB_URL",
    "MT_TICK_MAINTENANCE_URL",
)

_PAIRS = list(DATABASE_URL_FIELDS)


@pytest.fixture(autouse=True)
def _clear_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_URL_VARS:
        monkeypatch.delenv(var, raising=False)


def _settings(**fields: str | None) -> Settings:
    return Settings(_env_file=None, **fields)  # type: ignore[call-arg]


def test_env_var_names_are_exact() -> None:
    assert (
        env_var_for(Database.PRIMARY, Credential.APPLICATION) == "MT_TIMESCALE_DB_URL"
    )
    assert (
        env_var_for(Database.PRIMARY, Credential.MAINTENANCE)
        == "MT_TIMESCALE_MAINTENANCE_URL"
    )
    assert env_var_for(Database.TICK, Credential.APPLICATION) == "MT_TICK_DB_URL"
    assert (
        env_var_for(Database.TICK, Credential.MAINTENANCE) == "MT_TICK_MAINTENANCE_URL"
    )


def test_every_pair_is_mapped() -> None:
    assert set(_PAIRS) == {(d, c) for d in Database for c in Credential}


@pytest.mark.parametrize(("database", "credential"), _PAIRS)
def test_set_url_is_returned(database: Database, credential: Credential) -> None:
    field = DATABASE_URL_FIELDS[(database, credential)]
    url = f"postgresql://example.invalid/{field}"
    assert resolve_database_url(_settings(**{field: url}), database, credential) == url


@pytest.mark.parametrize(("database", "credential"), _PAIRS)
@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_raises_naming_variable(
    database: Database, credential: Credential, value: str | None
) -> None:
    field = DATABASE_URL_FIELDS[(database, credential)]
    with pytest.raises(DatabaseNotConfiguredError) as info:
        resolve_database_url(_settings(**{field: value}), database, credential)
    assert info.value.env_var == env_var_for(database, credential)
    assert str(info.value).startswith(f"{info.value.env_var} not configured.")


def test_tick_maintenance_never_falls_back_to_tick_application() -> None:
    settings = _settings(tick_db_url="postgresql://example.invalid/tick")
    with pytest.raises(DatabaseNotConfiguredError) as info:
        resolve_database_url(settings, Database.TICK, Credential.MAINTENANCE)
    assert info.value.env_var == "MT_TICK_MAINTENANCE_URL"


def test_tick_application_never_falls_back_to_primary() -> None:
    settings = _settings(
        timescale_db_url="postgresql://example.invalid/app",
        timescale_maintenance_url="postgresql://example.invalid/maint",
    )
    with pytest.raises(DatabaseNotConfiguredError) as info:
        resolve_database_url(settings, Database.TICK, Credential.APPLICATION)
    assert info.value.env_var == "MT_TICK_DB_URL"


# --- misroute guard (D5) ----------------------------------------------------

_MINUTE_IDS = [m["id"] for m in TRACKS["minute"][1:4]]
_FAKE_TICK_ID = "tick_001_fake"


@pytest.fixture
def fake_tick_track(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the tick track one non-bootstrap id, as slice 222 will."""
    registry = dict(migrations_pkg.TRACK_REGISTRY)
    registry["tick"] = TrackSpec(
        Database.TICK,
        [*TRACKS["tick"], {"id": _FAKE_TICK_ID, "description": "x", "sql": ""}],
    )
    monkeypatch.setattr(migrations_pkg, "TRACK_REGISTRY", registry)


def test_primary_ids_are_foreign_to_tick() -> None:
    assert foreign_ledger_ids(_MINUTE_IDS, Database.TICK) == sorted(_MINUTE_IDS)


@pytest.mark.usefixtures("fake_tick_track")
def test_tick_ids_are_foreign_to_primary() -> None:
    ids = [*_MINUTE_IDS, _FAKE_TICK_ID]
    assert foreign_ledger_ids(ids, Database.PRIMARY) == [_FAKE_TICK_ID]


@pytest.mark.parametrize("database", list(Database))
def test_bootstrap_is_never_foreign(database: Database) -> None:
    assert foreign_ledger_ids([BOOTSTRAP_MIGRATION_ID], database) == []


@pytest.mark.parametrize("database", list(Database))
def test_unknown_ids_are_ignored(database: Database) -> None:
    assert foreign_ledger_ids(["999_retired_long_ago"], database) == []


def test_own_ids_are_not_foreign() -> None:
    ids = [BOOTSTRAP_MIGRATION_ID, *_MINUTE_IDS]
    assert foreign_ledger_ids(ids, Database.PRIMARY) == []


def _stub_pool(regclass: Any, ledger_ids: list[str]) -> tuple[MagicMock, list[str]]:
    """A pool whose connection answers the guard's two SELECTs; records SQL."""
    executed: list[str] = []
    conn = MagicMock()

    def _execute(sql: str) -> MagicMock:
        executed.append(sql)
        result = MagicMock()
        if "to_regclass" in sql:
            result.fetchone.return_value = (regclass,)
        else:
            result.fetchall.return_value = [(i,) for i in ledger_ids]
        return result

    conn.execute.side_effect = _execute

    @contextmanager
    def _connection() -> Iterator[MagicMock]:
        yield conn

    pool = MagicMock()
    pool.connection.side_effect = _connection
    return pool, executed


def test_missing_ledger_passes_without_id_query() -> None:
    pool, executed = _stub_pool(None, [])
    assert_ledger_belongs(pool, Database.TICK, "tick", "MT_TICK_MAINTENANCE_URL")
    assert len(executed) == 1


def test_own_ledger_passes() -> None:
    pool, executed = _stub_pool("schema_migrations", [BOOTSTRAP_MIGRATION_ID])
    assert_ledger_belongs(pool, Database.TICK, "tick", "MT_TICK_MAINTENANCE_URL")
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in executed)


def test_foreign_ledger_raises_naming_variable_and_at_most_five_ids() -> None:
    ids = [m["id"] for m in TRACKS["minute"][1:9]]
    pool, _ = _stub_pool("schema_migrations", ids)
    with pytest.raises(LedgerMisrouteError) as info:
        assert_ledger_belongs(pool, Database.TICK, "tick", "MT_TICK_MAINTENANCE_URL")
    message = str(info.value)
    assert message.startswith("refusing: ")
    assert "routed to primary" in message
    assert "MT_TICK_MAINTENANCE_URL" in message
    assert sum(i in message for i in ids) == 5
    assert "3 more" in message
    assert info.value.foreign_ids == sorted(ids)
