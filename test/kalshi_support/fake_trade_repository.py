"""``FakeTradeRepository`` — in-memory twin of ``TradeRepository``.

Same public surface over sets and a state row. The fake cannot run the
rule's SQL, so the test declares which tickers are *unknown* (no catalog
row) and which are *excluded* (known, not selected); everything else is
selected. Proving the predicate itself against real rows is the integration
tier's job (``test_kalshi_trades.py``). Conflict-ignore, the set-once
coverage floor, and the ``(ticker, created_time, trade_id)`` key mirror the
real statements, so the core's accounting is tested for real.

``transaction()`` snapshots the state on entry and restores it when the block
raises, recording ``begin`` / ``commit`` / ``rollback`` in ``tx_log`` so
per-page granularity is testable; ``fail_on`` injects any exception on the
``at``-th call of a method. ``watermark_at_write`` records the watermark in
force at each ``write_page`` — the proof that it moves only after a window.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from manta_trading.data.kalshi.models import Trade
from manta_trading.data.kalshi.trade_repository import PageCounts, TradeState


@dataclass
class _State:
    trades: TradeState | None = None
    last_full_sync_at: datetime | None = None
    catalog_walk_start: datetime | None = None
    stored: set[tuple[str, datetime, str]] = field(default_factory=set)


class FakeTradeRepository:
    """See the module docstring."""

    def __init__(self) -> None:
        self._s = _State()
        #: Tickers with no catalog row / known but not selected by the rule.
        self.unknown_tickers: set[str] = set()
        self.excluded_tickers: set[str] = set()
        self.tx_log: list[str] = []
        self.pages: list[PageCounts] = []
        self.watermark_at_write: list[datetime | None] = []
        self._failures: list[tuple[str, BaseException, int]] = []
        self._counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Inspection and setup
    # ------------------------------------------------------------------

    @property
    def state(self) -> TradeState | None:
        return self._s.trades

    @state.setter
    def state(self, value: TradeState | None) -> None:
        self._s.trades = value

    @property
    def last_full_sync_at(self) -> datetime | None:
        return self._s.last_full_sync_at

    @property
    def catalog_walk_start(self) -> datetime | None:
        return self._s.catalog_walk_start

    @catalog_walk_start.setter
    def catalog_walk_start(self, value: datetime | None) -> None:
        self._s.catalog_walk_start = value

    @property
    def stored(self) -> set[tuple[str, datetime, str]]:
        return self._s.stored

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
    # TradeRepository surface
    # ------------------------------------------------------------------

    async def read_state(self) -> TradeState | None:
        self._enter("read_state")
        return self._s.trades

    async def init_state(self, cutoff: datetime) -> None:
        self._enter("init_state")
        if self._s.trades is None:
            self._s.trades = TradeState(watermark_ts=cutoff, coverage_from_ts=cutoff)

    async def advance_watermark(self, window_end: datetime) -> None:
        self._enter("advance_watermark")
        current = self._s.trades
        coverage = current.coverage_from_ts if current is not None else None
        self._s.trades = TradeState(watermark_ts=window_end, coverage_from_ts=coverage)

    async def set_last_full_sync(self, phase_start: datetime) -> None:
        self._enter("set_last_full_sync")
        self._s.last_full_sync_at = phase_start

    async def read_catalog_walk_start(self) -> datetime | None:
        self._enter("read_catalog_walk_start")
        return self._s.catalog_walk_start

    async def write_page(self, rows: Sequence[Trade]) -> PageCounts:
        self._enter("write_page")
        state = self._s.trades
        self.watermark_at_write.append(state.watermark_ts if state else None)
        unknown: list[str] = []
        excluded = selected = written = 0
        for row in rows:
            if row.ticker in self.unknown_tickers:
                unknown.append(row.ticker)
                continue
            if row.ticker in self.excluded_tickers:
                excluded += 1
                continue
            selected += 1
            key = (row.ticker, row.created_time, row.trade_id)
            if key not in self._s.stored:
                self._s.stored.add(key)
                written += 1
        counts = PageCounts(
            fetched=len(rows),
            unknown_market=len(unknown),
            excluded_by_rule=excluded,
            selected=selected,
            written=written,
            unknown_tickers=tuple(unknown),
        )
        self.pages.append(counts)
        return counts
