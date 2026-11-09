"""Served time bars: provider ``ohlcv-1m`` rows, rolled up per session (306 D2, D5).

One statement per :class:`TickWindow`, like the tick reads, over
``tick_ohlcv_1m`` on the key prefix ``(instrument_id, ts_event)``. Rows come
back in ``BAR_COLUMNS`` order with raw integers.

- ``1m`` is the stored rows, ``time`` the stored ``ts_event``.
- Coarser sizes group by a bucket anchored at the session open
  (:data:`SESSION_BUCKET`): first open, max high, min low, last close, summed
  volume. Empty buckets are omitted. ``1d`` is one bucket stamped at the open.

Ticks are never read here: no provider bars, no bars (306 D2).
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

from manta_trading.constants import GRANULARITY_BAR_MINUTES, Granularity
from manta_trading.data.tick.bar_constants import BAR_1M_TABLE
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import (
    WINDOW_COLUMNS,
    WINDOW_WHERE,
    OnBatch,
    TickWindow,
    stream_rows,
    window_params,
)

NS_PER_MINUTE = 60 * 1_000_000_000

#: The session-anchored bucket start, defined once (306 D2). ``bar_parity``
#: imports it so tick-derived minutes bucket exactly as the provider's do.
#: Integer division; ``ts_event >= open_ns`` inside a window, so never negative.
SESSION_BUCKET = sql.SQL(
    "%(open_ns)s::bigint"
    " + ((ts_event - %(open_ns)s::bigint) / %(width_ns)s::bigint)"
    " * %(width_ns)s::bigint"
)
#: ``1d`` is the whole session, stamped with its open.
_DAY_BUCKET = sql.SQL("%(open_ns)s::bigint")

_TABLE = sql.Identifier(BAR_1M_TABLE)

_STORED = sql.SQL(
    "SELECT ts_event, {window}, open, high, low, close, volume"
    " FROM {table} WHERE {where} ORDER BY ts_event"
)
#: ``ts_event`` is unique per instrument (the key), so open and close by it
#: are unambiguous.
_ROLLED = sql.SQL(
    "SELECT bucket, {window},"
    " (array_agg(open ORDER BY ts_event))[1], max(high), min(low),"
    " (array_agg(close ORDER BY ts_event DESC))[1],"
    " sum(volume)::bigint"  # numeric otherwise; a bar volume is an integer
    " FROM (SELECT {bucket} AS bucket, ts_event, open, high, low, close, volume"
    " FROM {table} WHERE {where}) AS minutes"
    " GROUP BY bucket ORDER BY bucket"
)


def bucket_params(window: TickWindow, width_ns: int) -> dict[str, Any]:
    """The window's parameters plus the bucket width."""
    return window_params(window) | {"width_ns": width_ns}


async def read_provider_bars(
    conn: Conn, window: TickWindow, granularity: Granularity, on_batch: OnBatch
) -> int:
    """The window's provider bars at ``granularity`` (``BAR_COLUMNS`` rows),
    empty buckets omitted; returns the row count."""
    if granularity is Granularity.M1:
        query = _STORED.format(window=WINDOW_COLUMNS, table=_TABLE, where=WINDOW_WHERE)
        return await stream_rows(conn, query, window_params(window), on_batch)
    if granularity is Granularity.D1:
        bucket, params = _DAY_BUCKET, window_params(window)
    else:
        width = GRANULARITY_BAR_MINUTES[granularity] * NS_PER_MINUTE
        bucket, params = SESSION_BUCKET, bucket_params(window, width)
    query = _ROLLED.format(
        window=WINDOW_COLUMNS, bucket=bucket, table=_TABLE, where=WINDOW_WHERE
    )
    return await stream_rows(conn, query, params, on_batch)
