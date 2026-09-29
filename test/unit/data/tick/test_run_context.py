"""Run-context refusals that fire before any database connection (223, FR1).

Refusals 5–7 (connect, pending migrations, lock) need a database and live in
``test/integration/data/test_tick_run_context.py``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from tick_support.fake_historical import FakeHistorical

from manta_trading.config import Settings
from manta_trading.data.tick.constants import (
    TICK_ARCHIVE_DIR_ENV,
    TICK_ENV_PREFIX,
    TICK_SPEND_CEILING_ENV,
)
from manta_trading.data.tick.databento.adapter import (
    DatabentoTickProvider,
    api_key_env,
)
from manta_trading.data.tick.run_context import (
    TickPreflightError,
    known_tick_env_names,
    open_tick_run,
)

MISSPELT = "MT_TICK_DATA_SPEND_CEILING_USD"
TICK_URL_ENV = "MT_TICK_DB_URL"


@pytest.fixture(autouse=True)
def no_tick_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's own ``MT_TICK_*`` exports must not reach these tests."""
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX):
            monkeypatch.delenv(name)


def _settings(archive: Path | None, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "databento_api_key": "db-test-key",
        "tick_db_url": "postgresql://unused.invalid/tick",
        "tick_archive_dir": archive,
    }
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[call-arg]


def _fake_provider(settings: Settings) -> DatabentoTickProvider:
    return DatabentoTickProvider(FakeHistorical(), httpx.Client())  # type: ignore[arg-type]


async def _refusal(settings: Settings, env_file: Path | None = None) -> str:
    with pytest.raises(TickPreflightError) as info:
        async with open_tick_run(
            settings, provider_factory=_fake_provider, env_file=env_file
        ):
            pass
    return str(info.value)


@pytest.fixture
def archive(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "tick-archive"
    root.mkdir()
    yield root
    root.chmod(0o755)  # a test may have made it read-only


def test_known_names_come_from_settings() -> None:
    names = known_tick_env_names()
    assert TICK_SPEND_CEILING_ENV in names
    assert TICK_ARCHIVE_DIR_ENV in names
    assert TICK_URL_ENV in names


async def test_misspelt_key_in_environment_names_the_closest(
    monkeypatch: pytest.MonkeyPatch, archive: Path
) -> None:
    monkeypatch.setenv(MISSPELT, "0.50")
    message = await _refusal(_settings(archive))
    assert MISSPELT in message
    assert f"did you mean {TICK_SPEND_CEILING_ENV}" in message


async def test_misspelt_key_only_in_env_file_is_refused(
    tmp_path: Path, archive: Path
) -> None:
    """The 2026-09-28 case: the typo sat in ``.env``, never in the process."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"{MISSPELT}=0.50\n")
    assert MISSPELT not in os.environ
    message = await _refusal(_settings(archive), env_file=env_file)
    assert f"{MISSPELT} (did you mean {TICK_SPEND_CEILING_ENV}?)" in message


async def test_missing_api_key_is_refused(archive: Path) -> None:
    settings = _settings(archive, databento_api_key=None)
    with pytest.raises(TickPreflightError, match=api_key_env()):
        async with open_tick_run(settings, env_file=None):
            pass


async def test_missing_tick_url_is_refused(archive: Path) -> None:
    message = await _refusal(_settings(archive, tick_db_url=None))
    assert TICK_URL_ENV in message


async def test_unset_archive_dir_is_refused() -> None:
    message = await _refusal(_settings(None))
    assert f"{TICK_ARCHIVE_DIR_ENV} is not set" in message


async def test_missing_archive_dir_is_refused_and_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-archive"
    message = await _refusal(_settings(missing))
    assert TICK_ARCHIVE_DIR_ENV in message
    assert "not an existing directory" in message
    assert not missing.exists()


@pytest.fixture
def read_only_archive(archive: Path) -> Path:
    archive.chmod(0o555)
    return archive


async def test_read_only_archive_dir_is_refused(read_only_archive: Path) -> None:
    message = await _refusal(_settings(read_only_archive))
    assert f"{TICK_ARCHIVE_DIR_ENV}={read_only_archive} is not writable" in message
