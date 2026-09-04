"""``HistoricalSync`` — the historical phase of the Kalshi pass (slice 267).

No httpx, no typer, no SQL: the core depends on a :class:`HistoricalSource`
and three repositories, and every count it reports comes from a repository
method or an inner core. One firing (design *Architecture*), in order under
one request cap sized from the client's budget (Decision 2):

0. the archive walk, until done, once (``historical_archive``);
1. behind-cutoff candles (``historical_candles``);
2. the historical row — seeded at the live floor on the first run
   (Criterion 2) — then the trades tape walked **backward** by 265's
   ``TradeSync.drain`` toward ``HISTORICAL_TRADES_FLOOR`` (Decision 5);
3. ``sync_state`` and one ``phase_finished`` event.

Failure taxonomy (Decision 6): a ``ProviderError`` or
``psycopg.OperationalError`` aborts the phase; a ``ProviderPermanentError``
on one market's candles is an item error (``PARTIAL``). ``drain`` emits no
event, so the ``trades`` phase never appears to run twice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from manta_trading.data.kalshi.candle_repository import CandleRepository
from manta_trading.data.kalshi.constants import (
    COLLECTED_CANDLE_PERIOD,
    HISTORICAL_TRADES_FLOOR,
    CandlePeriod,
)
from manta_trading.data.kalshi.events import (
    NullSyncEventSink,
    SyncEvent,
    SyncEventSink,
    SyncEventType,
    emit_in_thread,
)
from manta_trading.data.kalshi.historical_archive import walk_archive
from manta_trading.data.kalshi.historical_candles import PHASE, drain_candles
from manta_trading.data.kalshi.historical_types import (
    HistoricalResult,
    HistoricalSource,
    HistoricalTradeSource,
)
from manta_trading.data.kalshi.repository import CatalogRepository
from manta_trading.data.kalshi.selection import CollectionRule
from manta_trading.data.kalshi.trade_repository import TradeRepository, TradeState
from manta_trading.data.kalshi.trade_sync import TradeSync
from manta_trading.data.kalshi.trade_types import TradeResult, WindowDirection

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class HistoricalSync:
    """See the module docstring. One instance per phase run; ``cap`` is
    passed in — the core never sees the client (the phase computes it)."""

    def __init__(
        self,
        source: HistoricalSource,
        trades: TradeRepository | Any,
        candles: CandleRepository | Any,
        catalog: CatalogRepository | Any,
        sink: SyncEventSink | None = None,
        *,
        rule: CollectionRule,
        run_id: UUID,
        cap: int,
        clock: Callable[[], datetime] = _utc_now,
        period: CandlePeriod = COLLECTED_CANDLE_PERIOD,
    ) -> None:
        self.source = source
        self.trades = trades
        self.candles = candles
        self.catalog = catalog
        self._sink: SyncEventSink = sink if sink is not None else NullSyncEventSink()
        self.rule = rule
        self.cap = cap
        self.clock = clock
        self.period = period
        self.result = HistoricalResult(
            run_id=run_id, started_at=clock(), cap=cap, floor=HISTORICAL_TRADES_FLOOR
        )

    def cap_reached(self) -> bool:
        """Checked before every request-making unit (a page, a market, a
        window): the phase exceeds the cap by at most one unit."""
        return self.result.requests >= self.cap

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self) -> HistoricalResult:
        result = self.result
        phase_start = result.started_at
        try:
            logger.info(
                "kalshi historical phase started run_id=%s cap=%d floor=%s rule: %s",
                result.run_id,
                self.cap,
                result.floor.isoformat(),
                self.rule.describe(),
            )
            if await walk_archive(self):
                # Nothing downstream runs on a partial catalog (Decision 9).
                # The candle ceiling reads the row as it stands; the row is
                # seeded only afterwards, so a candles abort leaves it as
                # it was.
                existing = await self.trades.read_state()
                await drain_candles(self, floor_reached=self._at_floor(existing))
                state = await self._state()
                if state is not None:
                    await self._trades(state)
            async with self.trades.transaction():
                await self.trades.set_last_full_sync(phase_start)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.exception("kalshi historical phase aborted")
            await self._finish()
            raise
        await self._finish()
        return result

    # ------------------------------------------------------------------
    # The historical row (Criterion 2) and the trades sub-drain
    # ------------------------------------------------------------------

    async def _state(self) -> TradeState | None:
        """The historical row, seeded at the live floor on the first run;
        ``None`` when the live phase has never run (nothing to seed from)."""
        result = self.result
        state = await self.trades.read_state()
        if state is None or state.watermark_ts is None:
            live_floor = await self.trades.read_live_coverage_from()
            if live_floor is None:
                result.trades_row_missing = True
                logger.info(
                    "kalshi historical trades skipped: no live trades row to seed "
                    "the tape floor from (the trades phase has never run)"
                )
                return None
            async with self.trades.transaction():
                await self.trades.init_state(live_floor, result.floor)
            logger.info(
                "kalshi historical first run: tape from live floor %s down to %s",
                live_floor.isoformat(),
                result.floor.isoformat(),
            )
            state = TradeState(watermark_ts=live_floor, coverage_from_ts=result.floor)
        result.watermark_before = state.watermark_ts
        result.watermark_after = state.watermark_ts
        return state

    def _at_floor(self, state: TradeState | None) -> bool:
        return (
            state is not None
            and state.watermark_ts is not None
            and state.watermark_ts <= self.result.floor
        )

    async def _trades(self, state: TradeState) -> None:
        # 268 Decision 9, before the drain — and before the floor-reached
        # return, so a typo'd filter aborts loudly even once the backfill is
        # done (the production steady state).
        await self.trades.assert_trades_filter_known()
        result = self.result
        watermark = state.watermark_ts
        if watermark is None or self._at_floor(state):
            result.floor_reached = True
            logger.info(
                "kalshi historical floor reached: watermark %s <= floor %s; no "
                "trades request",
                watermark.isoformat() if watermark else None,
                result.floor.isoformat(),
            )
            return
        inner = TradeSync(
            HistoricalTradeSource(self.source),
            self.trades,
            NullSyncEventSink(),
            rule=self.rule,
            run_id=result.run_id,
            clock=self.clock,
            direction=WindowDirection.BACKWARD,
            cap=max(self.cap - result.requests, 0),
        )
        try:
            await inner.drain(watermark, result.floor)
        finally:
            # Copied even on an abort: the windows that committed before the
            # failure are real progress and the event's counts must say so.
            self._copy_trades(inner.result, watermark)
        inner.log_unknown_prefixes()

    def _copy_trades(self, walked: TradeResult, watermark: datetime) -> None:
        """The inner drain's figures, copied — never recomputed."""
        result = self.result
        result.requests += walked.requests
        result.capped = result.capped or walked.capped
        result.windows_completed = walked.windows_completed
        result.trades_fetched = walked.trades_fetched
        result.trades_written = walked.trades_written
        result.unknown_market = walked.unknown_market
        result.excluded_by_rule = walked.excluded_by_rule
        result.excluded_by_trades_filter = walked.excluded_by_trades_filter
        result.duplicates = walked.duplicates
        result.unknown_prefixes = dict(walked.unknown_prefixes)
        after = walked.watermark_after or watermark
        result.watermark_after = after
        result.floor_reached = after <= result.floor

    # ------------------------------------------------------------------
    # Finish and events
    # ------------------------------------------------------------------

    async def _finish(self) -> None:
        result = self.result
        elapsed = self.clock() - result.started_at
        result.duration_ms = int(elapsed.total_seconds() * 1000)
        event = SyncEvent(
            run_id=result.run_id,
            timestamp=self.clock(),
            event_type=SyncEventType.PHASE_FINISHED,
            phase=PHASE,
            counts=result.counts(),
            error=result.error,
            duration_ms=result.duration_ms,
        )
        await emit_in_thread(self._sink, event)
