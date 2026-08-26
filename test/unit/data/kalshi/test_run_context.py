"""Unit tests for the shared preflight context (slice 263, Task 1.3).

The client and the connection preflight are patched at the seams
``test_data_kalshi.py`` already uses; the subject is lifetime ownership and
which exceptions propagate.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from manta_trading.data.kalshi.auth import KalshiCredentialError
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.db import PreflightError
from manta_trading.data.kalshi.events import JsonlSyncEventSink, NullSyncEventSink
from manta_trading.data.kalshi.run_context import KalshiRun, open_kalshi_run


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.timescale_db_url = "postgresql://ts/db"
    return settings


@contextlib.contextmanager
def _patched(
    *,
    client_error: BaseException | None = None,
    preflight: BaseException | None = None,
) -> Iterator[tuple[MagicMock, MagicMock, AsyncMock]]:
    conn = MagicMock()
    conn.close = AsyncMock()
    client = MagicMock()
    client.aclose = AsyncMock()
    with (
        patch.object(
            KalshiClient,
            "from_settings",
            side_effect=client_error,
            return_value=client,
        ),
        patch(
            "manta_trading.data.kalshi.db.open_sync_connection",
            AsyncMock(return_value=conn, side_effect=preflight),
        ) as open_conn,
    ):
        yield client, conn, open_conn


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_yields_run_and_closes_everything(self):
        with _patched() as (client, conn, _):
            async with open_kalshi_run(_settings(), None) as run:
                assert isinstance(run, KalshiRun)
                assert isinstance(run.run_id, UUID)
                assert run.client is client
                assert run.conn is conn
                assert isinstance(run.sink, NullSyncEventSink)
                client.aclose.assert_not_awaited()
            client.aclose.assert_awaited_once()
            conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_events_file_selects_jsonl_sink_and_closes_it(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        with _patched():
            async with open_kalshi_run(_settings(), path) as run:
                sink = run.sink
                assert isinstance(sink, JsonlSyncEventSink)
                sink.emit(_event(run.run_id))
                assert sink._file is not None
            assert sink._file is None  # closed on exit
        assert path.read_text().count("\n") == 1

    @pytest.mark.asyncio
    async def test_each_run_gets_a_fresh_id(self):
        with _patched():
            async with open_kalshi_run(_settings(), None) as first:
                first_id = first.run_id
            async with open_kalshi_run(_settings(), None) as second:
                assert second.run_id != first_id


class TestPreflightFailures:
    @pytest.mark.asyncio
    async def test_connection_failure_closes_client_and_reraises(self):
        with _patched(preflight=PreflightError("lock held")) as (client, conn, _):
            with pytest.raises(PreflightError, match="lock held"):
                async with open_kalshi_run(_settings(), None):
                    pytest.fail("body must not run")
            client.aclose.assert_awaited_once()
            conn.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_credential_failure_never_opens_a_connection(self):
        with _patched(client_error=KalshiCredentialError("half a pair")) as (
            _,
            _conn,
            open_conn,
        ):
            with pytest.raises(KalshiCredentialError, match="half a pair"):
                async with open_kalshi_run(_settings(), None):
                    pytest.fail("body must not run")
            open_conn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_body_exception_still_releases_the_lock(self):
        with _patched() as (client, conn, _):
            with pytest.raises(RuntimeError):
                async with open_kalshi_run(_settings(), None):
                    raise RuntimeError("phase blew up")
            client.aclose.assert_awaited_once()
            conn.close.assert_awaited_once()


def _event(run_id: UUID):
    from datetime import UTC, datetime

    from manta_trading.data.kalshi.events import SyncEvent, SyncEventType

    return SyncEvent(
        run_id=run_id,
        timestamp=datetime.now(UTC),
        event_type=SyncEventType.RUN_STARTED,
    )
