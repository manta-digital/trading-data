"""The N most recent equity bars, read through bounded windows (issue #13).

"Latest N" is never one ``ORDER BY time DESC LIMIT N`` query: with no time
bound the planner considers every chunk of ``daily_ohlcv`` (2.5-4.0 s of
planning, slice 187 D1). Instead the read anchors at the symbol's last data
date, taken from the bounded coverage-plus-head lookup symbol detail uses, and
fetches a window sized from N. A window that comes up short (a sparse symbol,
a long gap) doubles until it holds N bars or reaches the symbol's first data
date. Each fetch goes through the existing readers, so adjustment and the
``prints`` rule apply exactly as for an explicit range.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from manta_trading.api_server.queries import (
    UniverseEdgeCache,
    fetch_symbol_coverage,
    fetch_symbol_head,
    merge_available_ranges,
)
from manta_trading.constants import (
    BARS_PER_TRADING_DAY,
    TRADING_DAYS_PER_CALENDAR_DAY,
    CycleGranularity,
    Granularity,
)

if TYPE_CHECKING:
    import pandas as pd
    import psycopg
    from psycopg_pool import ConnectionPool

#: Bars a request reads past N in its first window, as a multiple: holidays and
#: half days thin a window below the calendar average.
_SPAN_MARGIN = 1.5
#: Calendar days added to the first window, so a request for a handful of bars
#: still spans a weekend or a holiday run.
_SPAN_PAD_DAYS = 7

#: The granularities served from the minute family; the rest are daily.
MINUTE_GRAINS: frozenset[Granularity] = frozenset(
    {Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4}
)

Fetch = Callable[[date, date], Awaitable["pd.DataFrame"]]


def bars_per_calendar_day(granularity: Granularity) -> float:
    """Bars a dense symbol yields per calendar day at this granularity."""
    return BARS_PER_TRADING_DAY[granularity] * TRADING_DAYS_PER_CALENDAR_DAY


def family_of(granularity: Granularity) -> CycleGranularity:
    """The data family a granularity is served from."""
    if granularity in MINUTE_GRAINS:
        return CycleGranularity.MINUTE
    return CycleGranularity.DAILY


def first_span_days(granularity: Granularity, limit: int) -> int:
    """Calendar days the first window spans for ``limit`` bars."""
    per_day = bars_per_calendar_day(granularity)
    return math.ceil(limit / per_day * _SPAN_MARGIN) + _SPAN_PAD_DAYS


def available_ranges(
    pool: ConnectionPool[psycopg.Connection[Any]],
    edges: UniverseEdgeCache,
    symbol: str,
) -> dict[CycleGranularity, tuple[date, date]]:
    """The symbol's first and last data date per family; bounded reads only
    (the symbol-detail lookup, slice 187)."""
    with pool.connection() as conn:
        coverage = fetch_symbol_coverage(conn, symbol)
        head = fetch_symbol_head(conn, symbol, edges.get(conn))
    return merge_available_ranges(coverage, head)


@dataclass(frozen=True)
class LatestWindow:
    """The bars read and the inclusive date window they were read over."""

    bars: pd.DataFrame
    start: date
    end: date


async def read_latest(
    fetch: Fetch,
    held: tuple[date, date],
    granularity: Granularity,
    limit: int,
    end: date | None,
) -> LatestWindow | None:
    """The ``limit`` most recent bars at or before ``end`` (latest held when
    ``None``). ``held`` is the family's first and last data date. ``None``
    when nothing is held at or before ``end``."""
    first, last = held
    upper = last if end is None else min(end, last)
    if upper < first:
        return None
    span = first_span_days(granularity, limit)
    while True:
        lower = max(first, upper - timedelta(days=span - 1))
        bars = await fetch(lower, upper)
        if len(bars) >= limit or lower == first:
            return LatestWindow(bars.tail(limit), lower, upper)
        span *= 2
