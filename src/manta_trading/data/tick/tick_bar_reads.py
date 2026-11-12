"""History tick bars: a window's stored trades through the cutter (308 D3, D5, D10).

:func:`read_tick_bars` has the ``ReadWindow`` shape once its keyword-only
``ticks_per_bar``, ``bounds`` and ``stop`` are bound (``functools.partial``),
so ``read_series`` drives it as it drives ``read_ticks``. The route passes
``read_series`` no bounds: every window arrives unclipped, because a window's
bars are cut from its session open whatever ``from`` says, and a bar opened
before ``until`` is completed with the trades after it. This module applies
the bounds itself, with ``clip`` deciding which windows overlap them, as
admission does.

One fresh :class:`TickBarCutter` per window. Each fetched batch is pushed
through it in the default executor (milliseconds of CPU per batch) and awaited
before the next fetch, so one thread at a time touches the cutter; the awaited
future is the hand-off between the loop and the worker thread. The final
``close_session`` runs on the loop, which is safe only because no executor call
is in flight then; a change that overlaps fetch and push must move it into the
executor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import aclosing
from datetime import date

from psycopg import sql

from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import ledger_totals, shape_of
from manta_trading.data.tick.tick_bars import (
    TickBar,
    TickBarCutter,
    TickBarInputError,
    Trade,
)
from manta_trading.data.tick.tick_reads import (
    WINDOW_WHERE,
    Bounds,
    OnBatch,
    Row,
    TickWindow,
    clip,
    iter_batches,
    window_params,
)
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

#: Awaited before each fetch; true means the client has gone (D10).
Stop = Callable[[], Awaitable[bool]]

#: The served tick-bar row, in order: the bar's fields with the window's
#: ``raw_symbol`` and ``instrument_id`` after its times.
TICK_BAR_COLUMNS = (
    "session_date",
    "index",
    "open_time",
    "close_time",
    "raw_symbol",
    "instrument_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "buy_volume",
    "sell_volume",
    "bid_px",
    "ask_px",
    "partial",
)

#: The trade columns in ``Trade`` order, so a fetched row is ``Trade._make(row)``.
#: The BBO columns are null on trades-tier rows; ``Trade`` carries ``None``.
_TRADES = sql.SQL(
    "SELECT ts_event, sequence, sequence_ordinal, %(session_date)s::date,"
    " price, size, side, bid_px_00, ask_px_00"
    " FROM tick_trade WHERE {where}"
    " ORDER BY ts_event, sequence, sequence_ordinal"
).format(where=WINDOW_WHERE)


class TickBarWindowError(Exception):
    """A window's stored trades break the cutter's contract (D1). The cause is
    the ``TickBarInputError``, whose ``keys`` name the offending trades."""

    def __init__(self, window: TickWindow) -> None:
        super().__init__(
            f"stored ticks for session {window.session_date} of"
            f" {window.raw_symbol} (instrument {window.instrument_id}) cannot be"
            " cut into bars"
        )
        self.session_date: date = window.session_date
        self.raw_symbol: str = window.raw_symbol
        self.instrument_id: int = window.instrument_id


class ReadAbandoned(Exception):
    """``stop`` said the client has gone; nothing more is fetched (D10)."""


class TradeScanExceeded(Exception):
    """The windows to scan hold more trades than the limit (D5); ``422``."""

    def __init__(self, scanned: int, limit: int) -> None:
        super().__init__(
            f"the request scans {scanned:,} trades, over the {limit:,} trade"
            " limit; narrow start/end"
        )
        self.scanned = scanned
        self.limit = limit


def overlapping(windows: Sequence[TickWindow], bounds: Bounds) -> list[TickWindow]:
    """The windows, unclipped, that ``clip`` keeps for ``[from, until)`` (D3)."""
    return [w for w in windows if clip([w], bounds)]


class _WindowCut:
    """One window's cutting state; touched by one executor call at a time."""

    def __init__(self, window: TickWindow, ticks_per_bar: int, bounds: Bounds):
        self.cutter = TickBarCutter(ticks_per_bar)
        self.n = ticks_per_bar
        self.from_ns, self.until_ns = bounds
        self.position = 0  # 0-based index of the next trade in the session
        self.tail = (window.raw_symbol, window.instrument_id)

    def push(self, batch: Sequence[Row]) -> tuple[list[Row], bool]:
        """Cut one batch; return the kept bar rows and whether ``until`` is hit.

        The window is one session, so the trade at position ``i`` opens a bar
        exactly when ``i % n == 0``; one at or after ``until`` ends the scan.
        """
        bars: list[TickBar] = []
        until_ns, n = self.until_ns, self.n
        for row in batch:
            trade = Trade._make(row)
            if (
                until_ns is not None
                and self.position % n == 0
                and trade.ts_event >= until_ns
            ):
                return self.rows(bars), True
            bars.extend(self.cutter.push(trade))
            self.position += 1
        return self.rows(bars), False

    def rows(self, bars: Sequence[TickBar | None]) -> list[Row]:
        """Bars opened in ``[from, until)``, as ``TICK_BAR_COLUMNS`` rows."""
        from_ns, until_ns = self.from_ns, self.until_ns
        raw_symbol, instrument_id = self.tail
        return [
            (
                b.session_date,
                b.index,
                b.open_time,
                b.close_time,
                raw_symbol,
                instrument_id,
                b.open,
                b.high,
                b.low,
                b.close,
                b.volume,
                b.trade_count,
                b.buy_volume,
                b.sell_volume,
                b.bid_px,
                b.ask_px,
                b.partial,
            )  # fmt: skip
            for b in bars
            if b is not None
            and (from_ns is None or b.open_time >= from_ns)
            and (until_ns is None or b.open_time < until_ns)
        ]


async def read_tick_bars(
    conn: Conn,
    window: TickWindow,
    on_batch: OnBatch,
    *,
    ticks_per_bar: int,
    bounds: Bounds,
    stop: Stop,
) -> int:
    """The window's bars opened in ``[from, until)``, to ``on_batch`` in order;
    returns how many. ``window`` must span its session: bar indexes count from
    the session open. One that misses the bounds is not read.

    Raises:
        ValueError: ``window`` was clipped (``clip``), so its bars would be
            renumbered from the clip point.
        ReadAbandoned: ``stop()`` returned true before a fetch.
        TickBarWindowError: a stored trade broke the cutter's contract.
    """
    if window.clipped:
        raise ValueError(
            f"tick bars need the whole session; the {window.session_date} window"
            f" of {window.raw_symbol} was clipped"
        )
    if not overlapping([window], bounds):
        return 0
    state = _WindowCut(window, ticks_per_bar, bounds)
    loop = asyncio.get_running_loop()
    count = 0
    async with aclosing(iter_batches(conn, _TRADES, window_params(window))) as it:
        while True:
            if await stop():
                raise ReadAbandoned
            batch = await anext(it, None)
            if batch is None:
                break
            rows, done = await _cut(loop, state, window, batch)
            on_batch(rows)
            count += len(rows)
            if done:
                break
    last = state.rows([state.cutter.close_session()])
    on_batch(last)
    return count + len(last)


async def _cut(
    loop: asyncio.AbstractEventLoop,
    state: _WindowCut,
    window: TickWindow,
    batch: Sequence[Row],
) -> tuple[list[Row], bool]:
    """One batch through the cutter off the loop; input errors gain the window."""
    try:
        return await loop.run_in_executor(None, state.push, batch)
    except TickBarInputError as exc:
        raise TickBarWindowError(window) from exc


# -- admission (D5) -----------------------------------------------------------


def _entry(plan: GetPlan) -> TickUniverseEntry:
    product = plan.target.product
    for entry in TICK_UNIVERSE:
        if entry.product == product:
            return entry
    raise LookupError(f"product {product} of a built plan is not in the universe")


async def trades_to_scan(conn: Conn, plan: GetPlan, bounds: Bounds) -> int:
    """The ledger's record count over the windows a tick-bar read scans: exactly
    the rows those reads return (``mt data tick coverage`` checks it)."""
    windows = overlapping(plan.segments, bounds)
    if not windows:
        return 0
    first = min(w.session_date for w in windows)
    last = max(w.session_date for w in windows)
    totals = await ledger_totals(
        conn, shape_of(_entry(plan)), SchemaFamily.TICK_TIER, first, last
    )
    # No ledger row for a window means no trades held for it: it scans nothing.
    return sum(totals.get((w.instrument_id, w.session_date), 0) for w in windows)


def trade_admission(
    bounds: Bounds, limit: int
) -> Callable[[Conn, GetPlan], Awaitable[None]]:
    """``read_series``' ``admit`` hook: refuse before any tick is read."""

    async def admit(conn: Conn, plan: GetPlan) -> None:
        scanned = await trades_to_scan(conn, plan, bounds)
        if scanned > limit:
            raise TradeScanExceeded(scanned, limit)

    return admit
