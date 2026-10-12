"""Per-session reads of ``tick_trade``: raw ticks and session-bounded bars (230).

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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg import sql

from manta_trading.constants import GRANULARITY_BAR_MINUTES, Granularity
from manta_trading.data.tick.constants import TICK_GET_FETCH_ROWS
from manta_trading.data.tick.manifest_reads import Conn

_NS_PER_MINUTE = 60 * 1_000_000_000

#: TD5's tick row, in order. The BBO columns are null on trades-tier rows.
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

Row = tuple[Any, ...]
OnBatch = Callable[[Sequence[Row]], None]


@dataclass(frozen=True)
class TickWindow:
    """One session of one instrument: ticks with ``open_ns <= ts_event <
    close_ns``. The one segment type; ``get_plan`` plans these."""

    session_date: date
    instrument_id: int
    raw_symbol: str
    open_ns: int
    close_ns: int


#: The window's own columns, selected as parameters so rows need no reshaping.
_WINDOW_COLUMNS = sql.SQL(
    "%(session_date)s::date, %(raw_symbol)s::text, %(instrument_id)s::bigint"
)
_WINDOW_WHERE = sql.SQL(
    "instrument_id = %(instrument_id)s"
    " AND ts_event >= %(open_ns)s AND ts_event < %(close_ns)s"
)
_TICKS = sql.SQL(
    "SELECT ts_event, {window}, price, size, side, bid_px_00, ask_px_00,"
    " bid_sz_00, ask_sz_00, bid_ct_00, ask_ct_00, sequence"
    " FROM tick_trade WHERE {where}"
    " ORDER BY ts_event, sequence, sequence_ordinal"
)
#: Open and close by full key order: several trades can share a nanosecond.
_BARS = sql.SQL(
    "SELECT bucket, {window},"
    " (array_agg(price ORDER BY ts_event, sequence, sequence_ordinal))[1],"
    " max(price), min(price),"
    " (array_agg(price ORDER BY ts_event DESC, sequence DESC,"
    " sequence_ordinal DESC))[1],"
    " sum(size)"
    " FROM (SELECT {bucket} AS bucket, ts_event, sequence, sequence_ordinal,"
    " price, size FROM tick_trade WHERE {where}) AS t"
    " GROUP BY bucket ORDER BY bucket"
)
#: A bar never crosses the session open: the first bucket starts there.
_INTRADAY_BUCKET = sql.SQL(
    "GREATEST(time_bucket(%(width_ns)s::bigint, ts_event), %(open_ns)s::bigint)"
)
#: ``1d`` is the whole session, stamped with its open.
_SESSION_BUCKET = sql.SQL("%(open_ns)s::bigint")


def _params(window: TickWindow) -> dict[str, Any]:
    return {
        "session_date": window.session_date,
        "raw_symbol": window.raw_symbol,
        "instrument_id": window.instrument_id,
        "open_ns": window.open_ns,
        "close_ns": window.close_ns,
    }


async def _stream(
    conn: Conn, query: sql.Composed, params: dict[str, Any], on_batch: OnBatch
) -> int:
    """Run ``query`` on a named cursor; hand each batch on; return the count."""
    count = 0
    async with conn.cursor(name="tick_get") as cursor:
        await cursor.execute(query, params)
        while batch := await cursor.fetchmany(TICK_GET_FETCH_ROWS):
            on_batch(batch)
            count += len(batch)
    return count


async def read_ticks(conn: Conn, window: TickWindow, on_batch: OnBatch) -> int:
    """The window's ticks in key order (TD5 tick rows); returns the row count."""
    query = _TICKS.format(window=_WINDOW_COLUMNS, where=_WINDOW_WHERE)
    return await _stream(conn, query, _params(window), on_batch)


async def read_bars(
    conn: Conn, window: TickWindow, granularity: Granularity, on_batch: OnBatch
) -> int:
    """The window's bars (TD5 bar rows), empty buckets omitted; returns the
    row count."""
    params = _params(window)
    if granularity is Granularity.D1:
        bucket = _SESSION_BUCKET
    else:
        bucket = _INTRADAY_BUCKET
        params["width_ns"] = GRANULARITY_BAR_MINUTES[granularity] * _NS_PER_MINUTE
    query = _BARS.format(window=_WINDOW_COLUMNS, bucket=bucket, where=_WINDOW_WHERE)
    return await _stream(conn, query, params, on_batch)
