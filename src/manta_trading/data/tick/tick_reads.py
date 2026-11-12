"""Per-session reads of ``tick_trade``: raw ticks (230), and the window plumbing
the bar readers share (306).

LLD 230 TD3. One statement per :class:`TickWindow` (one session of one
instrument), each a range scan on the key prefix ``(instrument_id,
ts_event)``, over a server-side cursor fetched ``TICK_GET_FETCH_ROWS`` at a
time. Every batch goes to ``on_batch`` before the next fetch; nothing is
collected. Rows come back in TD5's column order with raw integers: formatting
(``tick_format``) is the writer's, so 231 can serialize its own way.

The caller owns the connection and its transaction (a named cursor needs
one). This module never opens, commits or closes anything.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from psycopg import sql

from manta_trading.data.tick.constants import TICK_GET_FETCH_ROWS
from manta_trading.data.tick.manifest_reads import Conn

#: TD5's tick row, in order. The BBO columns are null on trades-tier rows.
#: ``sequence_ordinal`` completes the stream key (308 D6).
TICK_COLUMNS = (
    "ts_event",
    "session_date",
    "raw_symbol",
    "instrument_id",
    "price",
    "size",
    "side",
    "bid_px",
    "ask_px",
    "bid_sz",
    "ask_sz",
    "bid_ct",
    "ask_ct",
    "sequence",
    "sequence_ordinal",
)
#: TD5's bar row, in order. ``time`` is the bucket start, in ns.
BAR_COLUMNS = (
    "time",
    "session_date",
    "raw_symbol",
    "instrument_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
#: The columns that hold fixed-point prices, for the writers.
TICK_PRICE_COLUMNS = frozenset({"price", "bid_px", "ask_px"})
BAR_PRICE_COLUMNS = frozenset({"open", "high", "low", "close"})
#: The columns that hold epoch nanoseconds, for the writers.
TIME_COLUMNS = frozenset({"ts_event", "time"})

Row = tuple[Any, ...]
OnBatch = Callable[[Sequence[Row]], None]


@dataclass(frozen=True)
class TickWindow:
    """One session of one instrument: ticks with ``open_ns <= ts_event <=
    close_ns``. Closed, as ``Session`` is: ingest files a tick stamped at the
    close under that session, so the ledger and ``coverage`` count it, and
    the read must too. The one segment type; ``get_plan`` plans these.
    ``clipped`` is true when ``clip`` narrowed it: it no longer spans its
    session, so a tick-bar read refuses it (308 D3)."""

    session_date: date
    instrument_id: int
    raw_symbol: str
    open_ns: int
    close_ns: int
    clipped: bool = False


#: Half-open instant bounds in epoch ns, ``[from, until)``; ``None`` = open.
Bounds = tuple[int | None, int | None]


def clip(windows: Sequence[TickWindow], bounds: Bounds) -> tuple[TickWindow, ...]:
    """Each window cut to ``[from, until)``; windows left empty are dropped.

    Windows are closed (``open_ns <= ts <= close_ns``), so ``until`` becomes
    ``until − 1``. Consecutive bounds ``[a, b)``, ``[b, c)`` therefore page
    through a session with no overlap and no gap (231 TD3; moved here
    from ``futures_read`` in 308 so the tick-bar reader shares it).
    """
    from_ns, until_ns = bounds
    clipped = []
    for window in windows:
        open_ns = window.open_ns if from_ns is None else max(window.open_ns, from_ns)
        close_ns = (
            window.close_ns if until_ns is None else min(window.close_ns, until_ns - 1)
        )
        if open_ns <= close_ns:
            narrowed = (open_ns, close_ns) != (window.open_ns, window.close_ns)
            clipped.append(
                replace(
                    window,
                    open_ns=open_ns,
                    close_ns=close_ns,
                    clipped=window.clipped or narrowed,
                )
            )
    return tuple(clipped)


#: The window's own columns, selected as parameters so rows need no reshaping
#: (``bar_reads`` and ``bar_parity`` share these).
WINDOW_COLUMNS = sql.SQL(
    "%(session_date)s::date, %(raw_symbol)s::text, %(instrument_id)s::bigint"
)
WINDOW_WHERE = sql.SQL(
    "instrument_id = %(instrument_id)s"
    " AND ts_event BETWEEN %(open_ns)s AND %(close_ns)s"
)
_TICKS = sql.SQL(
    "SELECT ts_event, {window}, price, size, side, bid_px_00, ask_px_00,"
    " bid_sz_00, ask_sz_00, bid_ct_00, ask_ct_00, sequence, sequence_ordinal"
    " FROM tick_trade WHERE {where}"
    " ORDER BY ts_event, sequence, sequence_ordinal"
)


def window_params(window: TickWindow) -> dict[str, Any]:
    return {
        "session_date": window.session_date,
        "raw_symbol": window.raw_symbol,
        "instrument_id": window.instrument_id,
        "open_ns": window.open_ns,
        "close_ns": window.close_ns,
    }


async def iter_batches(
    conn: Conn, query: sql.Composed, params: dict[str, Any]
) -> AsyncGenerator[list[Row]]:
    """Run ``query`` on a named cursor and yield it ``TICK_GET_FETCH_ROWS``
    rows at a time (308). Consume under ``contextlib.aclosing`` so a consumer
    that stops early closes the cursor at once, not at garbage collection."""
    async with conn.cursor(name="tick_get") as cursor:
        await cursor.execute(query, params)
        while batch := await cursor.fetchmany(TICK_GET_FETCH_ROWS):
            yield batch


async def stream_rows(
    conn: Conn, query: sql.Composed, params: dict[str, Any], on_batch: OnBatch
) -> int:
    """Run ``query`` on a named cursor; hand each batch on; return the count."""
    count = 0
    async with aclosing(iter_batches(conn, query, params)) as batches:
        async for batch in batches:
            on_batch(batch)
            count += len(batch)
    return count


async def read_ticks(conn: Conn, window: TickWindow, on_batch: OnBatch) -> int:
    """The window's ticks in key order (TD5 tick rows); returns the row count."""
    query = _TICKS.format(window=WINDOW_COLUMNS, where=WINDOW_WHERE)
    return await stream_rows(conn, query, window_params(window), on_batch)
