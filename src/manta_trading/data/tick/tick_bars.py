"""Trade-count bars: one trade in, closed bars out (slice 308 D1, D2).

The one implementation of the tick-bar rule. History (``tick_bar_reads``)
feeds it stored trades in a loop; the live stream (280) feeds it one trade at
a time. Standard library and ``dbn_values`` only, so it runs without the
database stack (an isolation test pins this).

The rule: one trade record is one tick. A bar closes at its Nth trade. The
count restarts at index 0 when a trade's session differs from the open bar's,
and the open bar is first emitted as that session's last bar, ``partial``.
``close_session`` ends the last session the same way. Input must arrive in key
order ``(ts_event, sequence, sequence_ordinal)``; anything else is a caller
bug and raises :class:`TickBarInputError`, never a silent reorder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from manta_trading.data.tick.dbn_values import (
    TRADE_SIDE_BUY,
    TRADE_SIDE_SELL,
    UNDEF_INT64,
)

#: A trade's stream key: ``(ts_event, sequence, sequence_ordinal)``.
TradeKey = tuple[int, int, int]


class TickBarInputError(ValueError):
    """A trade breaks the cutter's input contract (D1).

    ``keys`` is ``(previous, current)``: the previous trade's key (``None``
    for a window's first trade) and the offending trade's key.
    """

    def __init__(self, reason: str, keys: tuple[TradeKey | None, TradeKey]) -> None:
        super().__init__(f"{reason}: previous key {keys[0]}, current key {keys[1]}")
        self.keys = keys


class Trade(NamedTuple):
    """One trade record; field order is ``tick_bar_reads``' SELECT order."""

    ts_event: int
    sequence: int
    sequence_ordinal: int
    session_date: date
    price: int
    size: int
    side: str
    bid_px: int | None
    ask_px: int | None


@dataclass(frozen=True, slots=True)
class TickBar:
    """One bar (D2). Times are epoch ns, prices DBN fixed-point integers.

    A dataclass, not a ``NamedTuple``: the field ``index`` would shadow
    ``tuple.index``."""

    session_date: date
    index: int
    open_time: int
    close_time: int
    open: int
    high: int
    low: int
    close: int
    volume: int
    trade_count: int
    buy_volume: int
    sell_volume: int
    bid_px: int | None
    ask_px: int | None
    partial: bool


class _OpenBar:
    """The bar being filled; mutated per trade, so a plain slotted class."""

    __slots__ = (
        "session_date",
        "index",
        "open_time",
        "open",
        "high",
        "low",
        "volume",
        "trade_count",
        "buy_volume",
        "sell_volume",
        "last",
    )

    def __init__(self, index: int, trade: Trade) -> None:
        self.session_date = trade.session_date
        self.index = index
        self.open_time = trade.ts_event
        self.open = self.high = self.low = trade.price
        self.volume = self.trade_count = self.buy_volume = self.sell_volume = 0
        self.last = trade
        self.add(trade)

    def add(self, trade: Trade) -> None:
        price, size = trade.price, trade.size
        if price > self.high:
            self.high = price
        elif price < self.low:
            self.low = price
        self.volume += size
        self.trade_count += 1
        if trade.side == TRADE_SIDE_BUY:
            self.buy_volume += size
        elif trade.side == TRADE_SIDE_SELL:
            self.sell_volume += size
        self.last = trade

    def bar(self, partial: bool) -> TickBar:
        last = self.last
        return TickBar(
            self.session_date,
            self.index,
            self.open_time,
            last.ts_event,
            self.open,
            self.high,
            self.low,
            last.price,
            self.volume,
            self.trade_count,
            self.buy_volume,
            self.sell_volume,
            last.bid_px,
            last.ask_px,
            partial,
        )


class TickBarCutter:
    """Cuts one instrument's trades, in key order, into bars of N trades."""

    def __init__(self, ticks_per_bar: int) -> None:
        if ticks_per_bar < 1:
            raise ValueError(f"ticks_per_bar must be >= 1, got {ticks_per_bar}")
        self._n = ticks_per_bar
        self._open: _OpenBar | None = None
        self._next_index = 0
        self._session: date | None = None
        self._last_key: TradeKey | None = None

    def push(self, trade: Trade) -> list[TickBar]:
        """Add one trade; return the bars it closes (0, 1 or 2)."""
        self._check(trade)
        out: list[TickBar] = []
        if trade.session_date != self._session:
            if self._open is not None:
                out.append(self._open.bar(partial=True))
                self._open = None
            self._session = trade.session_date
            self._next_index = 0
        open_bar = self._open
        if open_bar is None:
            open_bar = self._open = _OpenBar(self._next_index, trade)
            self._next_index += 1
        else:
            open_bar.add(trade)
        if open_bar.trade_count == self._n:
            out.append(open_bar.bar(partial=False))
            self._open = None
        return out

    def current(self) -> TickBar | None:
        """The open bar, marked partial, without closing it; ``None`` if none."""
        return None if self._open is None else self._open.bar(partial=True)

    def close_session(self) -> TickBar | None:
        """End the session: its open bar as final and partial, or ``None``."""
        bar = self.current()
        self._open = None
        return bar

    def _check(self, trade: Trade) -> None:
        key = (trade.ts_event, trade.sequence, trade.sequence_ordinal)
        previous = self._last_key
        if trade.price == UNDEF_INT64:
            raise TickBarInputError("undefined price", (previous, key))
        if previous is not None and key <= previous:
            raise TickBarInputError("trade key not ascending", (previous, key))
        if self._session is not None and trade.session_date < self._session:
            raise TickBarInputError(
                f"session went backwards ({self._session} to {trade.session_date})",
                (previous, key),
            )
        self._last_key = key
