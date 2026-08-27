"""``FakeCandleRepository`` — in-memory twin of ``CandleRepository``.

Same public surface over dicts. The fake cannot run the rule's SQL, so each
market carries two flags the test declares — ``selected_recent`` and
``selected_ever`` — standing in for the two forms of the predicate; proving
the predicate itself against real rows is the integration tier's job
(``test_kalshi_candles.py``). The pending conditions, the backlog cap and
ordering, conflict-ignore, set-once coverage, and the two counts mirror the
real statements exactly, so the core's logic is tested for real.

``transaction()`` snapshots the state on entry and restores it when the block
raises, recording ``begin`` / ``commit`` / ``rollback`` in ``tx_log`` so
per-batch granularity is testable; ``fail_on`` injects any exception.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from manta_trading.data.kalshi.candle_plan import (
    PendingMarket,
    last_complete_period,
    period_span,
)
from manta_trading.data.kalshi.candle_repository import StateAdvance
from manta_trading.data.kalshi.constants import CandlePeriod, MarketStatus
from manta_trading.data.kalshi.models import Candlestick


@dataclass
class FakeMarket:
    ticker: str
    open_time: datetime
    close_time: datetime
    status: str = MarketStatus.ACTIVE.value
    settlement_ts: datetime | None = None
    selected_recent: bool = True
    selected_ever: bool = True


@dataclass
class StateRow:
    watermark_ts: datetime
    coverage_from_ts: datetime


@dataclass
class _State:
    markets: dict[str, FakeMarket] = field(default_factory=dict)
    state: dict[tuple[str, int], StateRow] = field(default_factory=dict)
    candles: dict[tuple[str, int, datetime], Candlestick] = field(default_factory=dict)
    sync_state: tuple[datetime, datetime] | None = None


class FakeCandleRepository:
    """See the module docstring."""

    def __init__(self) -> None:
        self._s = _State()
        self.tx_log: list[str] = []
        self.writes: list[tuple[str, int]] = []
        self._failures: list[tuple[str, BaseException, int]] = []
        self._counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Inspection and setup
    # ------------------------------------------------------------------

    @property
    def markets(self) -> dict[str, FakeMarket]:
        return self._s.markets

    @property
    def state(self) -> dict[tuple[str, int], StateRow]:
        return self._s.state

    @property
    def candles(self) -> dict[tuple[str, int, datetime], Candlestick]:
        return self._s.candles

    @property
    def sync_state(self) -> tuple[datetime, datetime] | None:
        """``(last_full_sync_at, watermark_ts)`` of the candlesticks surface."""
        return self._s.sync_state

    def add_market(self, market: FakeMarket) -> FakeMarket:
        self._s.markets[market.ticker] = market
        return market

    def fail_on(self, method: str, exc: BaseException, *, at: int = 1) -> None:
        """Raise ``exc`` on the ``at``-th call of ``method``."""
        self._failures.append((method, exc, at))

    def _enter(self, method: str) -> None:
        self._counts[method] = self._counts.get(method, 0) + 1
        for name, exc, at in self._failures:
            if name == method and self._counts[method] == at:
                raise exc

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        snapshot = copy.deepcopy(self._s)
        self.tx_log.append("begin")
        try:
            yield
        except BaseException:
            self._s = snapshot
            self.tx_log.append("rollback")
            raise
        self.tx_log.append("commit")

    # ------------------------------------------------------------------
    # Pending sets — the real statements' conditions, in Python
    # ------------------------------------------------------------------

    def _row(self, market: FakeMarket, period: CandlePeriod) -> PendingMarket:
        state = self._s.state.get((market.ticker, int(period)))
        return PendingMarket(
            market.ticker,
            market.open_time,
            market.close_time,
            state.watermark_ts if state else None,
        )

    def _pending(
        self, market: FakeMarket, period: CandlePeriod, phase_start: datetime
    ) -> bool:
        if market.open_time >= phase_start:
            return False
        state = self._s.state.get((market.ticker, int(period)))
        if state is None:
            return True
        end = min(
            market.close_time + period_span(period),
            last_complete_period(phase_start, period),
        )
        return state.watermark_ts < end

    async def pending_live(
        self, period: CandlePeriod, phase_start: datetime
    ) -> list[PendingMarket]:
        self._enter("pending_live")
        return [
            self._row(m, period)
            for m in sorted(self._s.markets.values(), key=lambda m: m.ticker)
            if m.status != MarketStatus.FINALIZED.value
            and m.selected_recent
            and self._pending(m, period, phase_start)
        ]

    async def pending_finishing(
        self, period: CandlePeriod, phase_start: datetime
    ) -> list[PendingMarket]:
        self._enter("pending_finishing")
        return [
            self._row(m, period)
            for m in sorted(self._s.markets.values(), key=lambda m: m.ticker)
            if m.status == MarketStatus.FINALIZED.value
            and m.selected_ever
            and (m.ticker, int(period)) in self._s.state
            and self._pending(m, period, phase_start)
        ]

    async def pending_backlog(
        self, period: CandlePeriod, phase_start: datetime, cutoff: datetime, limit: int
    ) -> list[PendingMarket]:
        self._enter("pending_backlog")
        rows = sorted(
            self._backlog(period, cutoff, since=True),
            key=lambda m: (m.settlement_ts or phase_start, m.ticker),
        )
        pending = [m for m in rows if self._pending(m, period, phase_start)]
        return [self._row(m, period) for m in pending[:limit]]

    def _backlog(
        self, period: CandlePeriod, cutoff: datetime, *, since: bool
    ) -> Iterable[FakeMarket]:
        for m in self._s.markets.values():
            if m.status != MarketStatus.FINALIZED.value or not m.selected_ever:
                continue
            if m.settlement_ts is None or (m.ticker, int(period)) in self._s.state:
                continue
            if (m.settlement_ts >= cutoff) == since:
                yield m

    async def count_backlog_remaining(
        self, period: CandlePeriod, cutoff: datetime
    ) -> int:
        self._enter("count_backlog_remaining")
        return sum(1 for _ in self._backlog(period, cutoff, since=True))

    async def count_behind_cutoff(self, period: CandlePeriod, cutoff: datetime) -> int:
        self._enter("count_behind_cutoff")
        return sum(1 for _ in self._backlog(period, cutoff, since=False))

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert_candles(
        self, period: CandlePeriod, candles: Iterable[tuple[str, Candlestick]]
    ) -> int:
        self._enter("insert_candles")
        written = 0
        for ticker, candle in candles:
            key = (ticker, int(period), candle.end_period_ts)
            if key not in self._s.candles:
                self._s.candles[key] = candle
                written += 1
        self.writes.append(("insert_candles", written))
        return written

    async def advance_state(
        self, period: CandlePeriod, advances: Sequence[StateAdvance]
    ) -> None:
        self._enter("advance_state")
        for advance in advances:
            key = (advance.ticker, int(period))
            existing = self._s.state.get(key)
            self._s.state[key] = StateRow(
                watermark_ts=advance.watermark_ts,
                coverage_from_ts=existing.coverage_from_ts
                if existing
                else advance.coverage_from_ts,
            )
        self.writes.append(("advance_state", len(advances)))

    async def set_sync_state(self, phase_start: datetime, cutoff: datetime) -> None:
        self._enter("set_sync_state")
        self._s.sync_state = (phase_start, cutoff)
        self.writes.append(("set_sync_state", 1))
