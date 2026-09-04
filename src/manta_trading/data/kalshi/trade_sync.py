"""``TradeSync`` — the trades phase of the Kalshi pass (slice 265).

No httpx, no typer, no SQL: the core depends on a :class:`TradeSource` and
a ``TradeRepository``, and every count it reports comes from a repository
method. One phase (design *Data Flow*): read the historical cutoff once →
the state row (created on the first run) → the pass bound from the catalog
walk → one-hour windows oldest-first, each page one transaction, the
watermark advanced once per fully walked window → ``sync_state`` → the
``phase_finished`` event. Sequential on the run's single connection
(Decision 9).

The window loop is :meth:`TradeSync.drain`, public and parameterised by
:class:`WindowDirection` (slice 267, Decision 5): ``run`` drives it forward
for the live tape; the historical core drives it backward over a
``historical``-surface repository and an adapter source, under its own
request cap, and owns its own event — ``drain`` emits none.

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
    WindowDirection,
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
        direction: WindowDirection = WindowDirection.FORWARD,
        cap: int = TRADE_REQUESTS_PER_PASS,
    ) -> None:
        self.source = source
        self.repository = repository
        self._sink: SyncEventSink = sink if sink is not None else NullSyncEventSink()
        self.rule = rule
        self.clock = clock
        self.direction = direction
        #: Decision 8 (265) / Decision 2 (267): page requests per run, checked
        #: before each window — a run exceeds it by at most one window.
        self.cap = cap
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
                # Decision 5: the pass bound trails the catalog walk's start.
                await self.drain(
                    state.watermark_ts, walk_start - TRADE_LATE_ARRIVAL_GUARD
                )
            async with self.repository.transaction():
                await self.repository.set_last_full_sync(phase_start)
            self.log_unknown_prefixes()
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
                await self.repository.init_state(cutoff, cutoff)
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

    async def drain(self, start: datetime, bound: datetime) -> None:
        """Steps 2–3: one-hour windows from ``start`` toward ``bound`` in
        this instance's direction, the watermark moved to each window's far
        edge once its last page committed. Forward: ``[start, start+1h)``
        up to the bound; backward: ``[start-1h, start)`` down to it — the
        last window is clamped to the bound either way. The cap is checked
        before each window, so a run exceeds it by at most one window.
        Emits no event: ``run`` owns the trades event, the historical core
        its own (slice 267)."""
        backward = self.direction is WindowDirection.BACKWARD
        while (start > bound) if backward else (start < bound):
            if self.result.requests >= self.cap:
                self.result.capped = True
                logger.info(
                    "kalshi %s cap reached: requests=%d >= %d; the next run "
                    "continues from watermark=%s",
                    self._label,
                    self.result.requests,
                    self.cap,
                    start.isoformat(),
                )
                return
            if backward:
                far_edge = max(start - TRADE_WINDOW, bound)
                await self._window(far_edge, start)
            else:
                far_edge = min(start + TRADE_WINDOW, bound)
                await self._window(start, far_edge)
            await self._advance(far_edge)
            start = far_edge

    @property
    def _label(self) -> str:
        """The repository's surface — what tells the two drains apart in the
        journal (``trades window …`` / ``historical window …``)."""
        return str(self.repository.surface)

    async def _advance(self, far_edge: datetime) -> None:
        """Data Flow step 5: the window is fully walked; the watermark moves
        to its far edge in its own transaction."""
        async with self.repository.transaction():
            await self.repository.advance_watermark(far_edge)
        self.result.watermark_after = far_edge
        self.result.windows_completed += 1

    # ------------------------------------------------------------------
    # One window (Data Flow steps 4–5)
    # ------------------------------------------------------------------

    async def _window(self, start: datetime, window_end: datetime) -> None:
        """Page through ``[start − overlap, window_end]``, one transaction
        per page. The caller moves the watermark afterwards (``_advance``),
        so it moves only after the last page — in either direction."""
        result = self.result
        window = PageCounts(0, 0, 0, 0, 0, 0)
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
        result.trades_fetched += window.fetched
        result.trades_written += window.written
        result.unknown_market += window.unknown_market
        result.excluded_by_rule += window.excluded_by_rule
        result.duplicates += window.duplicates
        logger.info(
            "%s window %s→%s pages %d fetched %d written %d unknown %d excluded %d",
            self._label,
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

    def log_unknown_prefixes(self) -> None:
        """The once-per-phase unknown-market line (Decision 5); the
        historical core calls it after ``drain`` (slice 267), so the line
        carries ``_label`` — the surface tells the two phases apart."""
        prefixes = self.result.unknown_prefixes
        if not prefixes:
            return
        listed = " · ".join(
            f"{prefix} {count:,}"
            for prefix, count in sorted(prefixes.items(), key=lambda kv: -kv[1])
        )
        logger.info("%s unknown markets: %s", self._label, listed)

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
        excluded_by_trades_filter=(
            total.excluded_by_trades_filter + page.excluded_by_trades_filter
        ),
        selected=total.selected + page.selected,
        written=total.written + page.written,
    )
