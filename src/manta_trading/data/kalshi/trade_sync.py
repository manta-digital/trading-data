"""``TradeSync`` — the trades phase of the Kalshi pass (slice 265).

No httpx, no typer, no SQL: the core depends on a :class:`TradeSource` and
a ``TradeRepository``, and every count it reports comes from a repository
method. One phase (design *Data Flow*): read the historical cutoff once →
the state row (created on the first run) → the pass bound from the catalog
walk → one-hour windows oldest-first, each page one transaction, the
watermark advanced once per fully walked window → ``sync_state`` → the
``phase_finished`` event. Sequential on the run's single connection
(Decision 9).

Failure taxonomy: a ``ProviderError`` aborts the phase (``PROVIDER_ABORT``);
``psycopg.OperationalError`` aborts it (``STORAGE_ABORT``); every other
``psycopg.Error`` propagates as a bug; ``TradesBehindCutoffError`` propagates
by design (Decision 6). There is no per-item failure, so the phase never
reports ``PARTIAL``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from manta_trading.data.kalshi.constants import (
    TRADE_LATE_ARRIVAL_GUARD,
    TRADE_PAGE_LIMIT,
    TRADE_REQUESTS_PER_PASS,
    TRADE_WINDOW,
    WINDOW_OVERLAP,
)
from manta_trading.data.kalshi.events import (
    NullSyncEventSink,
    SyncEvent,
    SyncEventSink,
    SyncEventType,
    emit_in_thread,
)
from manta_trading.data.kalshi.selection import CollectionRule
from manta_trading.data.kalshi.sync_types import epoch
from manta_trading.data.kalshi.trade_repository import (
    PageCounts,
    TradeRepository,
    TradeState,
)
from manta_trading.data.kalshi.trade_types import (
    TradeResult,
    TradesBehindCutoffError,
    TradeSource,
)

logger = logging.getLogger(__name__)

#: The phase name events carry — ``PassPhaseName.TRADES``'s value, spelled
#: here so this module does not import the pass (which imports it).
PHASE = "trades"
#: Decision 5: the unknown-market log line groups tickers by the text before
#: the first ``-``. Display only — nothing branches on a ticker's text.
_PREFIX_SEPARATOR = "-"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TradeSync:
    """See the module docstring. One instance per phase run."""

    def __init__(
        self,
        source: TradeSource,
        repository: TradeRepository | Any,
        sink: SyncEventSink | None = None,
        *,
        rule: CollectionRule,
        run_id: UUID,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.source = source
        self.repository = repository
        self._sink: SyncEventSink = sink if sink is not None else NullSyncEventSink()
        self.rule = rule
        self.clock = clock
        self.result = TradeResult(run_id=run_id, started_at=clock())

    # ------------------------------------------------------------------
    # Run (Data Flow steps 1–3, 6)
    # ------------------------------------------------------------------

    async def run(self) -> TradeResult:
        result = self.result
        phase_start = result.started_at
        try:
            cutoff = (await self.source.get_historical_cutoff()).trades_created_ts
            result.cutoff = cutoff
            state = await self._state(cutoff)
            # Logged every run: the cutoff line says how much live tape
            # remains, and the watermark where the drain stands.
            logger.info(
                "kalshi trades phase started run_id=%s cutoff=%s watermark=%s "
                "coverage_from=%s rule: %s",
                result.run_id,
                cutoff.isoformat(),
                state.watermark_ts.isoformat(),
                state.coverage_from_ts.isoformat(),
                self.rule.describe(),
            )
            walk_start = await self.repository.read_catalog_walk_start()
            if walk_start is None:
                # Decision 5: no catalog walk has completed, so there is no
                # bound every trade's market is guaranteed to be behind.
                result.catalog_missing = True
                logger.info(
                    "kalshi trades phase: no completed catalog walk; nothing fetched"
                )
            else:
                await self._windows(state.watermark_ts, walk_start)
            async with self.repository.transaction():
                await self.repository.set_last_full_sync(phase_start)
            self._log_unknown_prefixes()
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.exception("kalshi trades phase aborted")
            await self._finish()
            raise
        await self._finish()
        return result

    async def _state(self, cutoff: datetime) -> _Started:
        """Step 1: the state row — created at the cutoff on the first run
        (Decision 2); behind the cutoff is an abort (Decision 6)."""
        state: TradeState | None = await self.repository.read_state()
        if state is None:
            async with self.repository.transaction():
                await self.repository.init_state(cutoff)
            logger.info(
                "kalshi trades first run: the stored tape starts at the cutoff %s",
                cutoff.isoformat(),
            )
            state = TradeState(watermark_ts=cutoff, coverage_from_ts=cutoff)
        if state.watermark_ts is None or state.coverage_from_ts is None:
            raise RuntimeError(
                "kalshi.sync_state['trades'] exists without a watermark or "
                "coverage floor; the row is written only by init_state"
            )
        if state.watermark_ts < cutoff:
            raise TradesBehindCutoffError(state.watermark_ts, cutoff)
        started = _Started(state.watermark_ts, state.coverage_from_ts)
        self.result.coverage_from = started.coverage_from_ts
        self.result.watermark_before = started.watermark_ts
        self.result.watermark_after = started.watermark_ts
        return started

    async def _windows(self, watermark: datetime, walk_start: datetime) -> None:
        """Steps 2–3: windows oldest-first from the watermark up to the pass
        bound. Two bounds, two names: ``phase_end`` is where the pass stops
        (Decision 5); ``window_end`` is where one window stops. The cap is
        checked before each window, so a pass exceeds it by at most one
        window (Decision 8)."""
        phase_end = walk_start - TRADE_LATE_ARRIVAL_GUARD
        start = watermark
        while start < phase_end:
            if self.result.requests >= TRADE_REQUESTS_PER_PASS:
                self.result.capped = True
                logger.info(
                    "kalshi trades cap reached: requests=%d >= %d; the next pass "
                    "continues from watermark=%s",
                    self.result.requests,
                    TRADE_REQUESTS_PER_PASS,
                    start.isoformat(),
                )
                return
            window_end = min(start + TRADE_WINDOW, phase_end)
            await self._window(start, window_end)
            start = window_end

    # ------------------------------------------------------------------
    # One window (Data Flow steps 4–5)
    # ------------------------------------------------------------------

    async def _window(self, start: datetime, window_end: datetime) -> None:
        """Page through ``[start − overlap, window_end]``, one transaction
        per page; the watermark moves only after the last page."""
        result = self.result
        window = PageCounts(0, 0, 0, 0, 0)
        pages = 0
        cursor: str | None = None
        while True:
            page = await self.source.get_trades(
                cursor=cursor,
                # Decision 1: ``min_ts`` is a strict "after" at second
                # granularity, so the lower bound steps back by the overlap;
                # conflict-ignore makes the overlap free.
                min_ts=epoch(start - WINDOW_OVERLAP),
                max_ts=epoch(window_end),
                limit=TRADE_PAGE_LIMIT,
            )
            result.requests += 1
            pages += 1
            async with self.repository.transaction():
                counts: PageCounts = await self.repository.write_page(page.trades)
            window = _add(window, counts)
            self._tally_unknown(counts.unknown_tickers)
            if not page.cursor:
                break
            cursor = page.cursor
        async with self.repository.transaction():
            await self.repository.advance_watermark(window_end)
        result.watermark_after = window_end
        result.windows_completed += 1
        result.trades_fetched += window.fetched
        result.trades_written += window.written
        result.unknown_market += window.unknown_market
        result.excluded_by_rule += window.excluded_by_rule
        result.duplicates += window.duplicates
        logger.info(
            "trades window %s→%s pages %d fetched %d written %d unknown %d excluded %d",
            start.isoformat(),
            window_end.isoformat(),
            pages,
            window.fetched,
            window.written,
            window.unknown_market,
            window.excluded_by_rule,
        )

    def _tally_unknown(self, tickers: Iterable[str]) -> None:
        # Display only (Decision 5, CLAUDE.md): the prefix is what the
        # operator reads in the log line; no code path branches on it.
        prefixes = self.result.unknown_prefixes
        for ticker in tickers:
            prefix = ticker.split(_PREFIX_SEPARATOR, 1)[0]
            prefixes[prefix] = prefixes.get(prefix, 0) + 1

    def _log_unknown_prefixes(self) -> None:
        prefixes = self.result.unknown_prefixes
        if not prefixes:
            return
        listed = " · ".join(
            f"{prefix} {count:,}"
            for prefix, count in sorted(prefixes.items(), key=lambda kv: -kv[1])
        )
        logger.info("trades unknown markets: %s", listed)

    # ------------------------------------------------------------------
    # Finish and events (Data Flow step 6)
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


class _Started:
    """The state row after step 1, with both instants known to be set."""

    __slots__ = ("coverage_from_ts", "watermark_ts")

    def __init__(self, watermark_ts: datetime, coverage_from_ts: datetime) -> None:
        self.watermark_ts = watermark_ts
        self.coverage_from_ts = coverage_from_ts


def _add(total: PageCounts, page: PageCounts) -> PageCounts:
    """Window totals; the identity holds for a sum of pages that each hold it."""
    return PageCounts(
        fetched=total.fetched + page.fetched,
        unknown_market=total.unknown_market + page.unknown_market,
        excluded_by_rule=total.excluded_by_rule + page.excluded_by_rule,
        selected=total.selected + page.selected,
        written=total.written + page.written,
    )
