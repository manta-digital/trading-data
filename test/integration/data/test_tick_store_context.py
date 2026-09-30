"""The provider-free store context (slice 225, TD1).

``open_tick_store`` needs no Databento key and takes the lock it is given;
``connect_migrated`` (status and coverage) needs no archive directory and
takes no lock. 223's run-context refusals keep their own tests.
"""

from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import httpx
import pytest
from tick_support.fake_historical import FakeHistorical

from manta_trading.config import Settings
from manta_trading.data.tick.constants import (
    TICK_ACQUISITION_LOCK_KEY,
    TICK_ENV_PREFIX,
    TICK_INGEST_LOCK_KEY,
)
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.run_context import open_tick_run
from manta_trading.data.tick.store_context import (
    TickPreflightError,
    TickStore,
    connect_migrated,
    database_url,
    lock_held_message,
    open_tick_store,
)


@pytest.fixture(autouse=True)
def no_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No developer ``MT_TICK_*`` export and no Databento key reach these tests."""
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX):
            monkeypatch.delenv(name)
    monkeypatch.delenv("MT_DATABENTO_API_KEY", raising=False)


def _settings(url: str, archive: Path | None) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=url,
        tick_archive_dir=archive,
    )


def _store(
    url: str, archive: Path, lock_key: int = TICK_INGEST_LOCK_KEY
) -> AbstractAsyncContextManager[TickStore]:
    return open_tick_store(_settings(url, archive), lock_key=lock_key, env_file=None)


async def test_store_opens_without_a_databento_key(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    settings = _settings(migrated_tick_db, tmp_path)
    assert settings.databento_api_key is None
    async with _store(migrated_tick_db, tmp_path) as store:
        assert store.archive_root == tmp_path
        cursor = await store.conn.execute("SELECT 1")
        assert await cursor.fetchone() == (1,)


async def test_second_holder_of_the_same_key_is_refused(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    async with _store(migrated_tick_db, tmp_path):
        with pytest.raises(
            TickPreflightError, match=lock_held_message(TICK_INGEST_LOCK_KEY)
        ):
            async with _store(migrated_tick_db, tmp_path):
                pass


async def test_ingest_and_acquisition_locks_are_independent(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    def provider(settings: Settings) -> DatabentoTickProvider:
        return DatabentoTickProvider(FakeHistorical(), httpx.Client())  # type: ignore[arg-type]

    run_settings = _settings(migrated_tick_db, tmp_path)
    async with open_tick_run(run_settings, provider_factory=provider, env_file=None):
        async with _store(migrated_tick_db, tmp_path) as store:
            assert store.run_id


async def test_acquisition_key_is_refused_while_a_run_holds_it(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    async with _store(migrated_tick_db, tmp_path, TICK_ACQUISITION_LOCK_KEY):
        with pytest.raises(
            TickPreflightError, match=lock_held_message(TICK_ACQUISITION_LOCK_KEY)
        ):
            async with _store(migrated_tick_db, tmp_path, TICK_ACQUISITION_LOCK_KEY):
                pass


async def test_connect_migrated_needs_no_archive_and_takes_no_lock(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    settings = _settings(migrated_tick_db, None)
    async with _store(migrated_tick_db, tmp_path):
        conn = await connect_migrated(database_url(settings))
        try:
            cursor = await conn.execute("SELECT 1")
            assert await cursor.fetchone() == (1,)
        finally:
            await conn.close()


async def test_connect_migrated_refuses_a_bare_database(
    ephemeral_tick_db: str,
) -> None:
    with pytest.raises(TickPreflightError, match="mt data migrate apply --track tick"):
        await connect_migrated(ephemeral_tick_db)
