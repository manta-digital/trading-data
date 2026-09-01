"""``CandleSync`` — the candle phase of the Kalshi pass (slice 264).

No httpx, no typer, no SQL: the core depends on a :class:`CandleSource` and
a ``CandleRepository``, and every count it reports comes from a repository
method — it issues no SQL of its own. One phase (design *Data Flow*): read
the historical cutoff once → the three pending sets → targets → batches →
fetch and write one batch per transaction → ``sync_state`` → the
``phase_finished`` event. Sequential on the run's single connection
(Decision 9).

Failure taxonomy: a ``ProviderError`` on a batch aborts the phase — the
planner guarantees the endpoint's caps, so a 400 here is our bug (Decision
7). ``psycopg.IntegrityError`` on a batch re-writes it per market so only
the offending markets become item errors; ``OperationalError`` and every
other ``psycopg.Error`` propagate. A requested ticker absent from the
response is the one per-market failure the API signals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg

from manta_trading.data.kalshi.candle_plan import (
    CandleBatch,
    CandleTarget,
    PendingMarket,
    plan_batches,
    target_window,
)
from manta_trading.data.kalshi.candle_repository import CandleRepository, StateAdvance
from manta_trading.data.kalshi.candle_types import (
    CandleItemError,
    CandleResult,
    CandleSource,
)
from manta_trading.data.kalshi.constants import (
    CANDLE_BACKLOG_REQUESTS_PER_PASS,
    CANDLE_BATCH_MAX_CANDLES,
    CANDLE_BATCH_MAX_TICKERS,
    CANDLE_FIRST_SIGHT_LOOKBACK,
    CANDLE_PROGRESS_EVERY_REQUESTS,
    COLLECTED_CANDLE_PERIOD,
    CandlePeriod,
)
from manta_trading.data.kalshi.events import (
    NullSyncEventSink,
    SyncEvent,
    SyncEventSink,
    SyncEventType,
    emit_in_thread,
)
from manta_trading.data.kalshi.models import Candlestick
from manta_trading.data.kalshi.selection import CollectionRule
from manta_trading.data.kalshi.sync_types import epoch

logger = logging.getLogger(__name__)

#: The phase name events and item errors carry — ``PassPhaseName.CANDLES``'s
#: value, spelled here so this module does not import the pass (which
#: imports it).
PHASE = "candles"
#: Decision 7: the one per-market failure the batch endpoint signals.
NOT_SERVED = "not served by the batch endpoint"
#: Decision 6: rows the backlog query may return per pass.
BACKLOG_ROWS_PER_PASS = CANDLE_BACKLOG_REQUESTS_PER_PASS * CANDLE_BATCH_MAX_TICKERS


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CandleSync:
    """See the module docstring. One instance per phase run."""

    def __init__(
        self,
        source: CandleSource,
        repository: CandleRepository | Any,
        sink: SyncEventSink | None = None,
        *,
        rule: CollectionRule,
        run_id: UUID,
        clock: Callable[[], datetime] = _utc_now,
        period: CandlePeriod = COLLECTED_CANDLE_PERIOD,
    ) -> None:
        self.source = source
        self.repository = repository
        self._sink: SyncEventSink = sink if sink is not None else NullSyncEventSink()
        self.rule = rule
        self.clock = clock
        self.period = period
        self.result = CandleResult(run_id=run_id, started_at=clock(), period=period)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self) -> CandleResult:
        result = self.result
        phase_start = result.started_at
        try:
            cutoff = (await self.source.get_historical_cutoff()).market_settled_ts
            result.cutoff = cutoff
            # Logged every run, pending or not: the cutoff line is the
            # signal of how much the historical phase (267) has to backfill.
            logger.info(
                "kalshi candles phase started run_id=%s cutoff=%s candles rule: %s",
                result.run_id,
                cutoff.isoformat(),
                self.rule.describe(),
            )
            targets = await self._targets(phase_start, cutoff)
            batches = plan_batches(
                targets.values(),
                period=self.period,
                max_tickers=CANDLE_BATCH_MAX_TICKERS,
                max_candles=CANDLE_BATCH_MAX_CANDLES,
            )
            for batch in batches:
                await self._run_batch(batch, targets, len(batches))
            async with self.repository.transaction():
                await self.repository.set_sync_state(phase_start, cutoff)
            result.backlog_remaining = await self.repository.count_backlog_remaining(
                self.period, cutoff
            )
            result.behind_cutoff = await self.repository.count_behind_cutoff(
                self.period, cutoff
            )
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.exception("kalshi candles phase aborted")
            await self._finish()
            raise
        await self._finish()
        return result

    async def _targets(
        self, phase_start: datetime, cutoff: datetime
    ) -> dict[str, CandleTarget]:
        """Data Flow steps 2–3: the pending sets (only the backlog capped),
        mapped to fetch windows; markets with nothing to fetch drop out."""
        result = self.result
        live = await self.repository.pending_live(self.period, phase_start)
        finishing = await self.repository.pending_finishing(self.period, phase_start)
        backlog = await self.repository.pending_backlog(
            self.period, phase_start, cutoff, BACKLOG_ROWS_PER_PASS
        )
        result.pending_live = len(live)
        result.pending_finishing = len(finishing)
        result.pending_backlog = len(backlog)
        targets: dict[str, CandleTarget] = {}
        pending: list[PendingMarket] = [*live, *finishing, *backlog]
        for market in pending:
            target = target_window(
                market,
                phase_start=phase_start,
                period=self.period,
                lookback=CANDLE_FIRST_SIGHT_LOOKBACK,
            )
            if target is not None:
                targets[market.ticker] = target
        return targets

    # ------------------------------------------------------------------
    # One batch (Data Flow step 5)
    # ------------------------------------------------------------------

    async def _run_batch(
        self, batch: CandleBatch, targets: dict[str, CandleTarget], planned: int
    ) -> None:
        result = self.result
        served = await self.source.get_markets_candlesticks(
            batch.tickers,
            start_ts=epoch(batch.start),
            end_ts=epoch(batch.end),
            period_interval=self.period,
        )
        result.requests += 1
        result.markets_requested += len(batch.tickers)
        by_ticker = {entry.market_ticker: entry.candlesticks for entry in served}
        candles: dict[str, list[Candlestick]] = {}
        advances: list[StateAdvance] = []
        for ticker in batch.tickers:
            if ticker not in by_ticker:
                continue
            # Present — with or without candles — advances: Kalshi serves no
            # candle for an idle period (Decision 3), so an empty entry is a
            # complete answer for the window, not a missing one.
            candles[ticker] = by_ticker[ticker]
            target = targets[ticker]
            advances.append(
                StateAdvance(ticker, min(batch.end, target.close_end), target.start)
            )
        result.candles_fetched += sum(len(rows) for rows in candles.values())
        await self._write(candles, advances)
        for ticker in batch.tickers:
            if ticker not in by_ticker:
                await self.item_error(ticker, NOT_SERVED)
        if result.requests % CANDLE_PROGRESS_EVERY_REQUESTS == 0:
            logger.info(
                "kalshi candles progress requests=%d/%d markets=%d candles=%d",
                result.requests,
                planned,
                result.markets_advanced,
                result.candles_written,
            )

    async def _write(
        self, candles: dict[str, list[Candlestick]], advances: Sequence[StateAdvance]
    ) -> None:
        """One transaction per batch; on an integrity failure, one per market."""
        try:
            await self._write_markets(candles, advances)
        except psycopg.IntegrityError:
            logger.exception("kalshi candles batch integrity failure; per market")
            for advance in advances:
                one = {advance.ticker: candles[advance.ticker]}
                try:
                    await self._write_markets(one, [advance])
                except psycopg.IntegrityError as exc:
                    # Storage taxonomy (262): the offending market becomes an
                    # item error and the phase continues.
                    await self.item_error(advance.ticker, f"integrity: {exc}")

    async def _write_markets(
        self, candles: dict[str, list[Candlestick]], advances: Sequence[StateAdvance]
    ) -> None:
        rows = [
            (ticker, candle) for ticker, batch in candles.items() for candle in batch
        ]
        async with self.repository.transaction():
            written = await self.repository.insert_candles(self.period, rows)
            await self.repository.advance_state(self.period, advances)
        self.result.candles_written += written
        self.result.markets_advanced += len(advances)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def item_error(self, ticker: str, reason: str) -> None:
        logger.error("%s: %s skipped — %s", PHASE, ticker, reason)
        self.result.item_errors.append(CandleItemError(ticker, reason))
        await self._emit(SyncEventType.ITEM_ERROR, ticker=ticker, error=reason)

    async def _finish(self) -> None:
        result = self.result
        elapsed = self.clock() - result.started_at
        result.duration_ms = int(elapsed.total_seconds() * 1000)
        await self._emit(
            SyncEventType.PHASE_FINISHED,
            counts=result.counts(),
            error=result.error,
            duration_ms=result.duration_ms,
        )

    async def _emit(
        self,
        event_type: SyncEventType,
        *,
        counts: dict[str, int] | None = None,
        ticker: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        event = SyncEvent(
            run_id=self.result.run_id,
            timestamp=self.clock(),
            event_type=event_type,
            phase=PHASE,
            counts=counts or {},
            ticker=ticker,
            error=error,
            duration_ms=duration_ms,
        )
        await emit_in_thread(self._sink, event)
