"""Run a window reader to completion and hand back its rows (slice 306 tests)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.status_reads import calendar_sessions
from manta_trading.data.tick.tick_reads import BAR_COLUMNS, TickWindow


async def read_rows(url: str, read: Any, *args: Any) -> list[Sequence[Any]]:
    """Every row ``read(conn, *args, on_batch)`` yields, in one transaction."""
    rows: list[Sequence[Any]] = []
    async with await psycopg.AsyncConnection.connect(url) as conn:
        async with conn.transaction():
            await read(conn, *args, lambda batch: rows.extend(batch))
    return rows


def bar_dicts(rows: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    """``BAR_COLUMNS``-keyed dicts of bar rows."""
    return [dict(zip(BAR_COLUMNS, row, strict=True)) for row in rows]


def session_window(
    calendar_url: str, day: date, instrument_id: int, raw_symbol: str = "ESZ4"
) -> TickWindow:
    """The CME equity session dated ``day`` as a window for ``instrument_id``."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    found = calendar_sessions(
        calendar_url,
        calendar_for_product("ES"),
        start,
        start + timedelta(days=1),
    )
    (session,) = [s for s in found if s.session_date == day]
    return TickWindow(
        day,
        instrument_id,
        raw_symbol,
        utc_ns(session.open_utc),
        utc_ns(session.close_utc),
    )
