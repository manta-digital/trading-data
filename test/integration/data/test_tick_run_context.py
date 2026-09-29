"""Run-context refusals that need a database (slice 223; LLD 224 TD2, FR1).

Pending migrations, the advisory lock, and an unreachable database. The
provider is the real adapter over a fake SDK client, so nothing can spend.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
from tick_support.fake_historical import FakeHistorical

from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_ENV_PREFIX
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.run_context import (
    LOCK_HELD,
    TickPreflightError,
    open_tick_run,
)


@pytest.fixture(autouse=True)
def no_tick_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX):
            monkeypatch.delenv(name)


def _settings(url: str, archive: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        databento_api_key="db-test-key",
        tick_db_url=url,
        tick_archive_dir=archive,
    )


def _fake_provider(settings: Settings) -> DatabentoTickProvider:
    return DatabentoTickProvider(FakeHistorical(), httpx.Client())  # type: ignore[arg-type]


def _run(url: str, archive: Path) -> Any:
    return open_tick_run(
        _settings(url, archive), provider_factory=_fake_provider, env_file=None
    )


def _closed_port() -> int:
    """A local port nothing listens on (bound, then released)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_bare_database_refuses_with_the_migrate_command(
    ephemeral_tick_db: str, tmp_path: Path
) -> None:
    with pytest.raises(TickPreflightError, match="mt data migrate apply --track tick"):
        async with _run(ephemeral_tick_db, tmp_path):
            pass


async def test_migrated_database_yields_a_run(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    async with _run(migrated_tick_db, tmp_path) as run:
        assert run.archive_root == tmp_path
        assert run.run_id
        cursor = await run.conn.execute("SELECT 1")
        assert await cursor.fetchone() == (1,)


async def test_second_run_refuses_on_the_lock_until_the_first_closes(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    async with _run(migrated_tick_db, tmp_path):
        with pytest.raises(TickPreflightError, match=LOCK_HELD):
            async with _run(migrated_tick_db, tmp_path):
                pass
    async with _run(migrated_tick_db, tmp_path) as run:
        assert run.run_id


async def test_unreachable_database_names_the_variable(tmp_path: Path) -> None:
    url = f"postgresql://nobody@127.0.0.1:{_closed_port()}/tick"
    with pytest.raises(TickPreflightError, match="MT_TICK_DB_URL.*unreachable"):
        async with _run(url, tmp_path):
            pass
