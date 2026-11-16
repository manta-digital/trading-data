"""Served time bars, one session at a time, from provider bars or ticks (309).

A request's sessions each read from the source ``time_bar_source`` chose.
Sessions cut from ticks are admitted on the trades they read, from the ingest
ledger, before any tick is read, as tick bars are (308 D5).
"""

from __future__ import annotations

from collections.abc import Sequence

from manta_trading.data.tick.bar_constants import TICKS_SOURCE
from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_bar_reads import overlapping, trades_in_windows
from manta_trading.data.tick.tick_reads import Bounds, TickWindow
from manta_trading.data.tick.time_bar_source import Sources


class TimeBarScanExceeded(Exception):
    """The tick-cut sessions hold more trades than the limit; ``422``."""

    def __init__(self, scanned: int, limit: int, size: str) -> None:
        super().__init__(
            f"the request reads {scanned:,} trades to cut {size} bars, over the"
            f" {limit:,} trade limit; narrow start/end"
        )
        self.scanned = scanned
        self.limit = limit


def tick_cut_windows(
    plan: GetPlan, sources: Sources, bounds: Bounds
) -> list[TickWindow]:
    """The planned windows cut from ticks that meet ``[from, until)``."""
    return [
        w
        for w in overlapping(plan.segments, bounds)
        if sources.get(w.session_date) == TICKS_SOURCE
    ]


async def time_bar_trades(
    conn: Conn, plan: GetPlan, sources: Sources, bounds: Bounds
) -> int:
    """The ledger's trades over the tick-cut windows; provider-served and
    unread sessions add nothing, nor does a window with no ledger row."""
    windows: Sequence[TickWindow] = tick_cut_windows(plan, sources, bounds)
    return await trades_in_windows(conn, plan, windows)
