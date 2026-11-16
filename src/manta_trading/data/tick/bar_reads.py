"""Time bars from held provider ``ohlcv-1m`` rows, rolled up per session (306, 309).

One statement per :class:`TickWindow`, like the tick reads, over
``tick_ohlcv_1m`` on the key prefix ``(instrument_id, ts_event)``. Rows come
back in ``BAR_COLUMNS`` order with raw integers.

- A size equal to the provider width is the stored rows, ``time`` the stored
  ``ts_event``.
- Any wider size the provider width divides groups by a bucket anchored at the
  session open (:data:`SESSION_BUCKET`): first open, max high, min low, last
  close, summed volume. Empty buckets are omitted. ``1d`` is one bucket stamped
  at the open.

This module reads provider bars only; which source a session's bars come from
is ``time_bar_source``'s choice, and bars cut from ticks are ``time_bar_cut``'s.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

from manta_trading.data.tick.bar_constants import BAR_1M_TABLE, PROVIDER_BAR_WIDTH_NS
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import (
    WINDOW_COLUMNS,
    WINDOW_WHERE,
    Bounds,
    OnBatch,
    TickWindow,
    stream_rows,
    window_params,
)
from manta_trading.data.tick.time_bar_size import BarSize, provider_schema_for

NS_PER_MINUTE = 60 * 1_000_000_000

#: The session-anchored bucket start, defined once (306 D2). ``time_bar_cut``
#: imports it so bars cut from ticks bucket exactly as the provider's do.
#: Integer division; ``ts_event >= anchor_ns`` inside a window, so never negative.
SESSION_BUCKET = sql.SQL(
    "%(anchor_ns)s::bigint"
    " + ((ts_event - %(anchor_ns)s::bigint) / %(width_ns)s::bigint)"
    " * %(width_ns)s::bigint"
)
#: ``1d`` is the whole session, stamped with its open.
DAY_BUCKET = sql.SQL("%(anchor_ns)s::bigint")

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


def scan_params(
    window: TickWindow, width_ns: int | None, bounds: Bounds = (None, None)
) -> dict[str, Any] | None:
    """The window's parameters for a bucketed read, or ``None`` when no bucket
    starts in ``[from, until)``.

    ``anchor_ns`` is the session open and ``width_ns`` the bucket width (the
    whole session for ``None``, the session day). The scan (``open_ns`` to
    ``close_ns``) is narrowed to the trades of the buckets that start inside the
    bounds, each kept whole: a bar is selected by its start, never cut.

    Raises:
        ValueError: ``window`` was clipped, so its open is not the session's.
    """
    if window.clipped:
        raise ValueError(
            f"time bars need the whole session; the {window.session_date} window"
            f" of {window.raw_symbol} was clipped"
        )
    anchor, close = window.open_ns, window.close_ns
    width = close - anchor + 1 if width_ns is None else width_ns
    from_ns, until_ns = bounds
    low = anchor
    if from_ns is not None and from_ns > anchor:
        low = anchor - (anchor - from_ns) // width * width  # first start >= from
    high = close
    if until_ns is not None:
        if until_ns <= anchor:
            return None
        last_start = anchor + (until_ns - 1 - anchor) // width * width
        high = min(close, last_start + width - 1)
    if low > high:
        return None
    return window_params(window) | {
        "anchor_ns": anchor,
        "width_ns": width,
        "open_ns": low,
        "close_ns": high,
    }


async def read_provider_bars(
    conn: Conn,
    window: TickWindow,
    size: BarSize,
    on_batch: OnBatch,
    bounds: Bounds = (None, None),
) -> int:
    """The window's provider bars at ``size`` (``BAR_COLUMNS`` rows) that start
    in ``[from, until)``, empty buckets omitted; returns the row count.

    Raises:
        ValueError: no provider width divides ``size``.
    """
    if provider_schema_for(size) is not TickSchema.OHLCV_1M:
        raise ValueError(f"{size.token} bars cannot be made from ohlcv-1m bars")
    stored_width = PROVIDER_BAR_WIDTH_NS[TickSchema.OHLCV_1M]
    params = scan_params(window, size.width_ns, bounds)
    if params is None:
        return 0
    if size.width_ns == stored_width:
        query = _STORED.format(window=WINDOW_COLUMNS, table=_TABLE, where=WINDOW_WHERE)
    else:
        bucket = DAY_BUCKET if size.session_day else SESSION_BUCKET
        query = _ROLLED.format(
            window=WINDOW_COLUMNS, bucket=bucket, table=_TABLE, where=WINDOW_WHERE
        )
    return await stream_rows(conn, query, params, on_batch)
