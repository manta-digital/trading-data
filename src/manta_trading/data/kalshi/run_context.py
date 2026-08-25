"""Shared preflight context for a Kalshi run (slice 263, Decision 1).

``mt data kalshi sync`` and ``mt data kalshi pass`` differ only in what they
do *after* preflight: build the client, open and lock the connection, open
the event sink, mint a ``run_id``. :func:`open_kalshi_run` owns those four
lifetimes and yields them as a :class:`KalshiRun`; both commands are then
"open the context, run, print".

This lives in the data package rather than the CLI because a
``PassPhase.run`` takes a ``KalshiRun`` — the phase contract in
``collection_pass.py`` must be able to import it. Nothing here maps an
exception to an exit code: ``KalshiCredentialError`` and ``PreflightError``
propagate, and the CLI turns them into exit 1 in exactly one place.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from manta_trading.data.kalshi.events import (
    JsonlSyncEventSink,
    NullSyncEventSink,
    SyncEventSink,
)

if TYPE_CHECKING:
    from pathlib import Path

    import psycopg

    from manta_trading.config import Settings
    from manta_trading.data.kalshi.client import KalshiClient


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class KalshiRun:
    """One run's shared resources: what every phase of a pass is handed."""

    settings: Settings
    client: KalshiClient
    conn: psycopg.AsyncConnection[Any]
    sink: SyncEventSink
    run_id: UUID
    clock: Callable[[], datetime] = _utc_now


@contextlib.asynccontextmanager
async def open_kalshi_run(
    settings: Settings, events_file: Path | None
) -> AsyncIterator[KalshiRun]:
    """Preflight, yield the run, then release the client, lock, and sink.

    Raises ``KalshiCredentialError`` (bad credential pair) or
    ``PreflightError`` (database unreachable, track not applied, lock held)
    before yielding; the caller maps both to exit 1.
    """
    from manta_trading.data.kalshi.client import KalshiClient
    from manta_trading.data.kalshi.db import open_sync_connection

    client = KalshiClient.from_settings(settings)
    try:
        conn = await open_sync_connection(str(settings.timescale_db_url))
    except BaseException:
        await client.aclose()
        raise
    sink: SyncEventSink = (
        JsonlSyncEventSink(events_file) if events_file else NullSyncEventSink()
    )
    try:
        yield KalshiRun(
            settings=settings,
            client=client,
            conn=conn,
            sink=sink,
            run_id=uuid4(),
        )
    finally:
        await client.aclose()
        await conn.close()
        if isinstance(sink, JsonlSyncEventSink):
            sink.close()
