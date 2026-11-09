"""Tick-derived one-minute bars and their comparison with the provider's (306 D7).

The only surviving use of bars built from ``tick_trade``: ``mt data tick
parity`` sets them against ``tick_ohlcv_1m`` where both are held. Buckets are
the served ones (``bar_reads.SESSION_BUCKET`` at a 60 s width), so a tick minute
and a provider minute meet on the same key.

- :func:`read_tick_minute_bars` is the former ``read_bars`` SQL at one minute.
- :func:`classify_minutes` joins the two sides by bucket and classes each minute.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from psycopg import sql

from manta_trading.data.tick.bar_reads import (
    NS_PER_MINUTE,
    SESSION_BUCKET,
    bucket_params,
)
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import (
    BAR_COLUMNS,
    WINDOW_COLUMNS,
    WINDOW_WHERE,
    OnBatch,
    Row,
    TickWindow,
    stream_rows,
)

#: Open and close by full key order: several trades can share a nanosecond.
_TICK_MINUTES = sql.SQL(
    "SELECT bucket, {window},"
    " (array_agg(price ORDER BY ts_event, sequence, sequence_ordinal))[1],"
    " max(price), min(price),"
    " (array_agg(price ORDER BY ts_event DESC, sequence DESC,"
    " sequence_ordinal DESC))[1],"
    " sum(size)::bigint"  # numeric otherwise; a bar volume is an integer
    " FROM (SELECT {bucket} AS bucket, ts_event, sequence, sequence_ordinal,"
    " price, size FROM tick_trade WHERE {where}) AS t"
    " GROUP BY bucket ORDER BY bucket"
)


async def read_tick_minute_bars(
    conn: Conn, window: TickWindow, on_batch: OnBatch
) -> int:
    """The window's trades as one-minute bars (``BAR_COLUMNS`` rows), session
    anchored; returns the row count."""
    query = _TICK_MINUTES.format(
        window=WINDOW_COLUMNS, bucket=SESSION_BUCKET, where=WINDOW_WHERE
    )
    params = bucket_params(window, NS_PER_MINUTE)
    return await stream_rows(conn, query, params, on_batch)


class MinuteClass(StrEnum):
    """How one minute compares across the two sides."""

    AGREE = "agree"  # open, high, low, close and volume all equal
    DIFFER = "differ"  # both sides hold it; some of the five fields differ
    PROVIDER_ONLY = "provider_only"
    TICK_ONLY = "tick_only"


#: The five compared fields, in ``BAR_COLUMNS`` order after the identity columns.
COMPARED_FIELDS = BAR_COLUMNS[BAR_COLUMNS.index("open") :]
#: One side's minute: open, high, low, close, volume.
Ohlcv = tuple[int, ...]


@dataclass(frozen=True)
class MinuteRecord:
    """One minute of one session. ``at_close`` marks the bucket stamped at the
    session's close (issue #70)."""

    bucket: int
    cls: MinuteClass
    fields: tuple[str, ...]  # the differing fields, for ``DIFFER``
    tick: Ohlcv | None
    provider: Ohlcv | None
    at_close: bool


def _by_bucket(rows: Sequence[Row]) -> dict[int, Ohlcv]:
    first = BAR_COLUMNS.index("open")
    return {int(row[0]): tuple(int(v) for v in row[first:]) for row in rows}


def _classify(
    tick: Ohlcv | None, provider: Ohlcv | None
) -> tuple[MinuteClass, tuple[str, ...]]:
    if tick is None:
        return MinuteClass.PROVIDER_ONLY, ()
    if provider is None:
        return MinuteClass.TICK_ONLY, ()
    differing = tuple(
        name
        for name, a, b in zip(COMPARED_FIELDS, tick, provider, strict=True)
        if a != b
    )
    return (MinuteClass.DIFFER if differing else MinuteClass.AGREE), differing


def classify_minutes(
    tick_rows: Sequence[Row], provider_rows: Sequence[Row], close_ns: int
) -> list[MinuteRecord]:
    """Every minute either side holds, by bucket, classed. Both inputs are
    ``BAR_COLUMNS`` rows of one window; nothing on either side is nothing."""
    tick, provider = _by_bucket(tick_rows), _by_bucket(provider_rows)
    records = []
    for bucket in sorted(tick.keys() | provider.keys()):
        cls, fields = _classify(tick.get(bucket), provider.get(bucket))
        records.append(
            MinuteRecord(
                bucket,
                cls,
                fields,
                tick.get(bucket),
                provider.get(bucket),
                bucket == close_ns,
            )
        )
    return records
