"""Database identity and URL resolution (slice 923 D2).

Every ``Settings`` here is built with ``_env_file=None`` so a developer's
``.env`` cannot leak a URL into a no-fallback assertion.
"""

from __future__ import annotations

import pytest

from manta_trading.config import Settings
from manta_trading.market.schema.databases import (
    DATABASE_URL_FIELDS,
    Credential,
    Database,
    DatabaseNotConfiguredError,
    env_var_for,
    resolve_database_url,
)

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
