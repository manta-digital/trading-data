"""Build a ``TickRun`` around a fake provider for the pass tests (slice 224).

The preflight (``open_tick_run``) is 223's and has its own tests; the pass
tests need a run they fully control: a real autocommit connection to a
migrated tick database, the fake provider, a temp archive and a fake clock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from tick_support.fake_provider import FakeClock, FakeTickProvider

from manta_trading.config import Settings
from manta_trading.data.tick.run_context import TickRun


@asynccontextmanager
async def connect(url: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """An autocommit connection, as ``open_tick_run`` opens one."""
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        yield conn


def tick_run(
    conn: psycopg.AsyncConnection[Any],
    provider: FakeTickProvider,
    archive: Path,
    clock: FakeClock,
    **settings: Any,
) -> TickRun:
    return TickRun(
        settings=Settings(_env_file=None, **settings),  # type: ignore[call-arg]
        provider=provider,  # type: ignore[arg-type]
        conn=conn,
        archive_root=archive,
        run_id=uuid4(),
        clock=clock,
    )
