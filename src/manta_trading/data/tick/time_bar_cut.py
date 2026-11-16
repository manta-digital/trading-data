"""Time bars cut from ``tick_trade``: one statement per session (slice 309).

The one implementation of bars built from trades at a time size; the served
time-bar reader and the minute comparison (``bar_parity``) both call it. Buckets
are the provider reader's (``bar_reads.SESSION_BUCKET``, anchored at the session
open; ``DAY_BUCKET`` for ``1d``), so a tick bar and a provider bar of the same
size meet on the same key. A bar never crosses a session; an empty bucket has no
row. Open and close follow the full tick order: several trades can share a
nanosecond.

Rows are ``TIME_BAR_CUT_COLUMNS``: ``BAR_COLUMNS`` plus the bucket's trade count,
which the served reader checks against the ingest ledger and strips.
"""

from __future__ import annotations

from typing import Final

from psycopg import sql

from manta_trading.data.tick.bar_reads import DAY_BUCKET, SESSION_BUCKET, scan_params
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import (
    BAR_COLUMNS,
    WINDOW_COLUMNS,
    WINDOW_WHERE,
    Bounds,
    OnBatch,
    TickWindow,
    stream_rows,
)
from manta_trading.data.tick.time_bar_size import BarSize

#: The cut's row: a bar, then the number of trades in it.
TIME_BAR_CUT_COLUMNS: Final[tuple[str, ...]] = (*BAR_COLUMNS, "trade_count")

_CUT = sql.SQL(
    "SELECT bucket, {window},"
    " (array_agg(price ORDER BY ts_event, sequence, sequence_ordinal))[1],"
    " max(price), min(price),"
    " (array_agg(price ORDER BY ts_event DESC, sequence DESC,"
    " sequence_ordinal DESC))[1],"
    " sum(size)::bigint,"  # numeric otherwise; a bar volume is an integer
    " count(*)"
    " FROM (SELECT {bucket} AS bucket, ts_event, sequence, sequence_ordinal,"
    " price, size FROM tick_trade WHERE {where}) AS t"
    " GROUP BY bucket ORDER BY bucket"
)


async def cut_time_bars(
    conn: Conn,
    window: TickWindow,
    size: BarSize,
    on_batch: OnBatch,
    bounds: Bounds = (None, None),
) -> int:
    """The window's trades as ``size`` bars (``TIME_BAR_CUT_COLUMNS`` rows) that
    start in ``[from, until)``, each whole; returns the row count. ``window``
    must span its session (``scan_params``)."""
    params = scan_params(window, size.width_ns, bounds)
    if params is None:
        return 0
    bucket = DAY_BUCKET if size.session_day else SESSION_BUCKET
    query = _CUT.format(window=WINDOW_COLUMNS, bucket=bucket, where=WINDOW_WHERE)
    return await stream_rows(conn, query, params, on_batch)
