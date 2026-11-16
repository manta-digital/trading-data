"""Served time bars, one session at a time, from provider bars or ticks (309).

One :class:`TimeBarRead` per request. Its ``admit`` (``read_series``' hook)
chooses each planned session's source in the read's snapshot and refuses,
before any tick is read, when the tick-cut sessions hold more trades than the
limit (as tick bars are admitted, 308 D5). Its ``read`` has the ``ReadWindow``
shape: each window from its chosen source, ``[from, until)`` applied here by bar
start, so windows arrive unclipped (``scan_params``).

A tick-cut window's rows are collected, then stripped of their trade count in
the default executor (tens of thousands of rows is more than a millisecond on
the loop), checked against the ingest ledger, and only then handed on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date

from manta_trading.data.tick.bar_constants import TICKS_SOURCE
from manta_trading.data.tick.bar_reads import read_provider_bars
from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import ledger_totals, shape_of
from manta_trading.data.tick.tick_bar_reads import (
    ReadAbandoned,
    Stop,
    overlapping,
    plan_entry,
    trades_in_windows,
)
from manta_trading.data.tick.tick_reads import Bounds, OnBatch, Row, TickWindow
from manta_trading.data.tick.time_bar_cut import TIME_BAR_CUT_COLUMNS, cut_time_bars
from manta_trading.data.tick.time_bar_size import BarSize
from manta_trading.data.tick.time_bar_source import Sources, choose_sources

_COUNT = TIME_BAR_CUT_COLUMNS.index("trade_count")


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


class LedgerCountExceeded(Exception):
    """A session's tick cut read more trades than its ledger count: the stored
    ticks and the ledger disagree. Logged, ``500``."""

    def __init__(self, window: TickWindow, ledger: int, read: int) -> None:
        super().__init__(
            f"session {window.session_date} of {window.raw_symbol} (instrument"
            f" {window.instrument_id}) read {read:,} trades, over its ledger"
            f" count {ledger:,}"
        )
        self.session_date: date = window.session_date
        self.raw_symbol = window.raw_symbol
        self.instrument_id = window.instrument_id
        self.ledger = ledger
        self.read = read


def _strip(raw: Sequence[Row]) -> tuple[list[Row], int]:
    """The bar rows without their trade count, and the trades summed."""
    return [row[:_COUNT] for row in raw], sum(row[_COUNT] for row in raw)


async def _never() -> bool:
    return False


class TimeBarRead:
    """One request's time-bar read. ``max_trades=None`` admits anything (the
    CLI); ``stop`` is awaited before each window (the route's disconnect)."""

    def __init__(
        self,
        size: BarSize,
        calendar_url: str,
        *,
        max_trades: int | None,
        bounds: Bounds = (None, None),
        stop: Stop = _never,
    ) -> None:
        self.size = size
        self.calendar_url = calendar_url
        self.max_trades = max_trades
        self.bounds = bounds
        self.stop = stop
        self.sources: dict[date, str | None] = {}
        self.returned: set[date] = set()  # sessions that produced a bar
        self._plan: GetPlan | None = None

    async def admit(self, conn: Conn, plan: GetPlan) -> None:
        """Choose every session's source; refuse an over-limit cut."""
        self._plan = plan
        self.sources = await choose_sources(conn, self.calendar_url, plan, self.size)
        if self.max_trades is None:
            return
        scanned = await time_bar_trades(conn, plan, self.sources, self.bounds)
        if scanned > self.max_trades:
            raise TimeBarScanExceeded(scanned, self.max_trades, self.size.token)

    async def read(self, conn: Conn, window: TickWindow, on_batch: OnBatch) -> int:
        """The window's bars from its source; returns how many."""
        if await self.stop():
            raise ReadAbandoned
        source = self.sources.get(window.session_date)
        if source is None:
            return 0
        if source == TICKS_SOURCE:
            count = await self._cut(conn, window, on_batch)
        else:
            count = await read_provider_bars(
                conn, window, self.size, on_batch, self.bounds
            )
        if count:
            self.returned.add(window.session_date)
        return count

    async def _cut(self, conn: Conn, window: TickWindow, on_batch: OnBatch) -> int:
        raw: list[Row] = []
        await cut_time_bars(conn, window, self.size, raw.extend, self.bounds)
        loop = asyncio.get_running_loop()
        bars, traded = await loop.run_in_executor(None, _strip, raw)
        ledger = await self._ledger_count(conn, window)
        if traded > ledger:
            raise LedgerCountExceeded(window, ledger, traded)
        if bars:  # a batch is never empty, as ``stream_rows`` hands them on
            on_batch(bars)
        return len(bars)

    async def _ledger_count(self, conn: Conn, window: TickWindow) -> int:
        assert self._plan is not None, "admit runs before read"
        day = window.session_date
        totals = await ledger_totals(
            conn, shape_of(plan_entry(self._plan)), SchemaFamily.TICK_TIER, day, day
        )
        return totals.get((window.instrument_id, day), 0)
