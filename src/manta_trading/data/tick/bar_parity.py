"""Tick-derived one-minute bars and their comparison with the provider's (306 D7).

``mt data tick parity`` sets them against ``tick_ohlcv_1m`` where both are
held. The minutes come from ``time_bar_cut``, the one statement that cuts time
bars from ticks, so a tick minute and a provider minute meet on the same key.

- :func:`read_tick_minute_bars` is that cut at one minute.
- :func:`classify_minutes` joins the two sides by bucket and classes each minute.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import (
    BAR_COLUMNS,
    OnBatch,
    Row,
    TickWindow,
)
from manta_trading.data.tick.time_bar_cut import cut_time_bars
from manta_trading.data.tick.time_bar_size import parse_bar_size

_ONE_MINUTE = parse_bar_size("1m")
_BAR_WIDTH = len(BAR_COLUMNS)


async def read_tick_minute_bars(
    conn: Conn, window: TickWindow, on_batch: OnBatch
) -> int:
    """The window's trades as one-minute bars (``BAR_COLUMNS`` rows), session
    anchored; returns the row count."""

    def drop_trade_count(batch: Sequence[Row]) -> None:
        on_batch([row[:_BAR_WIDTH] for row in batch])

    return await cut_time_bars(conn, window, _ONE_MINUTE, drop_trade_count)


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
