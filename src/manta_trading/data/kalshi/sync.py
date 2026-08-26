"""``CatalogSync`` — the Kalshi catalog pass (slice 262).

No httpx, no typer, no SQL: the core depends on a :class:`CatalogSource`
(what it needs from ``KalshiClient``) and a ``CatalogRepository``. The CLI
and slice 263's pass unit call the same :meth:`CatalogSync.run`.

One run (design *Data Flow*): series → markets walk → events refresh →
settled stream (``sync_settled``) → awaiting reconciliation
(``sync_awaiting``) → state. Storage failures follow the design's taxonomy:
``IntegrityError`` on a page → the page is re-written row by row and the
offending rows become item errors; ``OperationalError`` and every other
``psycopg.Error`` propagate. Provider errors propagate. Both reach the CLI
through :func:`classify`, which never returns an integer — exit codes live
in ``cli/commands/kalshi.py`` only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from datetime import UTC, datetime
from itertools import batched
from typing import Any, Protocol, Unpack
from uuid import UUID, uuid4

from manta_trading.data.kalshi.client import EventsQuery, MarketsQuery
from manta_trading.data.kalshi.constants import (
    CATALOG_WALK_FILTERS,
    EVENTS_PAGE_LIMIT,
    KALSHI_MVE_FILTER,
    MARKETS_PAGE_LIMIT,
    TICKERS_BATCH_SIZE,
    WINDOW_OVERLAP,
    Surface,
)
from manta_trading.data.kalshi.events import (
    NullSyncEventSink,
    SyncEvent,
    SyncEventSink,
    SyncEventType,
)
from manta_trading.data.kalshi.models import (
    Event,
    EventsPage,
    HistoricalCutoff,
    Market,
    MarketsPage,
    Series,
)
from manta_trading.data.kalshi.repository import CatalogRepository, SyncState
from manta_trading.data.kalshi.sync_awaiting import reconcile_awaiting
from manta_trading.data.kalshi.sync_settled import drain_settled
from manta_trading.data.kalshi.sync_types import (
    ItemError,
    Page,
    PhaseCounts,
    SyncOutcome,
    SyncPhase,
    SyncResult,
    classify,
    epoch,
    paged,
    transitions_as_dict,
)
from manta_trading.data.kalshi.sync_writer import write_page
from manta_trading.providers.errors import ProviderPermanentError

__all__ = [
    "CatalogSource",
    "CatalogSync",
    "ItemError",
    "PhaseCounts",
    "SyncOutcome",
    "SyncPhase",
    "SyncResult",
    "classify",
    "epoch",
    "paged",
]

logger = logging.getLogger(__name__)


class CatalogSource(Protocol):
    """The client calls the sync core uses (design: *CatalogSource protocol*).

    ``get_event`` joined the six listed in the design after the live
    rehearsal (2026-08-25): ``GET /events?tickers=`` silently omits some
    events — 366 of ~14.7k parents, older events whose markets are still
    live — while ``GET /events/{ticker}`` returns them. Omitted tickers are
    fetched singly, the same per-item shape Decision 9 uses for series.

    ``KalshiClient`` satisfies it structurally; tests substitute a
    fixture-backed fake that records every received query.
    """

    async def get_series_list(self) -> list[Series]: ...

    async def get_series(self, series_ticker: str) -> Series: ...

    def iter_markets(self, **query: Unpack[MarketsQuery]) -> AsyncIterator[Market]: ...

    async def get_markets(
        self, *, cursor: str | None = None, **query: Unpack[MarketsQuery]
    ) -> MarketsPage: ...

    async def get_events(
        self, *, cursor: str | None = None, **query: Unpack[EventsQuery]
    ) -> EventsPage: ...

    async def get_event(self, event_ticker: str) -> Event: ...

    async def get_historical_cutoff(self) -> HistoricalCutoff: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CatalogSync:
    """See the module docstring. One instance per run."""

    def __init__(
        self,
        source: CatalogSource,
        repository: CatalogRepository | Any,
        sink: SyncEventSink | None = None,
        clock: Callable[[], datetime] = _utc_now,
        run_id: UUID | None = None,
    ) -> None:
        self.source = source
        self.repository = repository
        self._sink: SyncEventSink = sink if sink is not None else NullSyncEventSink()
        self.clock = clock
        self.seen: set[str] = set()
        self.captured: set[str] = set()
        self.series_known: set[str] = set()
        self.state: SyncState | None = None
        # A pass hands every phase its own run_id so one --events-file reads
        # as one run (design 263, Decision 3); a bare sync mints its own.
        self.result = SyncResult(run_id=run_id or uuid4(), started_at=clock())

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self, settled_since: datetime | None = None) -> SyncResult:
        result = self.result
        await self.emit(SyncEventType.RUN_STARTED)
        try:
            self.state = await self.repository.get_sync_state(Surface.CATALOG)
            await self._sync_series()
            await self._walk_markets()
            await self._refresh_events()
            await drain_settled(self, settled_since)
            await reconcile_awaiting(self)
            async with self.repository.transaction():
                await self.repository.set_last_full_sync(
                    Surface.CATALOG, result.started_at
                )
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.exception("kalshi catalog sync aborted")
            await self._finish()
            raise
        await self._finish()
        return result

    async def _finish(self) -> None:
        elapsed = self.clock() - self.result.started_at
        self.result.duration_ms = int(elapsed.total_seconds() * 1000)
        await self.emit(SyncEventType.RUN_FINISHED, error=self.result.error)

    async def emit(
        self,
        event_type: SyncEventType,
        *,
        phase: SyncPhase | None = None,
        counts: dict[str, int] | None = None,
        transitions: dict[tuple[str, str], int] | None = None,
        ticker: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Best-effort emission: a sink failure is logged, never aborts the run.

        The sink call runs in a worker thread (code review 262 F001): a
        ``JsonlSyncEventSink`` does a synchronous open/write/flush, which the
        project's async rule keeps off the event loop. The core is a single
        sequential writer, so one sink call at a time reaches the thread.
        """
        event = SyncEvent(
            run_id=self.result.run_id,
            timestamp=self.clock(),
            event_type=event_type,
            phase=str(phase) if phase else None,
            counts=counts or {},
            transitions=transitions_as_dict(transitions or {}),
            ticker=ticker,
            error=error,
            duration_ms=duration_ms,
        )
        try:
            await asyncio.to_thread(self._sink.emit, event)
        except Exception:
            logger.exception("event sink failed on %s", event_type)

    async def phase_finished(
        self, phase: SyncPhase, started: datetime, **extra: int
    ) -> None:
        counts = {**self.result.phases[phase].to_dict(), **extra}
        elapsed = int((self.clock() - started).total_seconds() * 1000)
        await self.emit(
            SyncEventType.PHASE_FINISHED,
            phase=phase,
            counts=counts,
            transitions=self.result.transitions,
            duration_ms=elapsed,
        )

    async def item_error(self, phase: SyncPhase, ticker: str, reason: str) -> None:
        logger.error("%s: %s skipped — %s", phase, ticker, reason)
        self.result.item_errors.append(ItemError(ticker, phase, reason))
        self.result.phases[phase].skipped += 1
        await self.emit(
            SyncEventType.ITEM_ERROR, phase=phase, ticker=ticker, error=reason
        )

    # ------------------------------------------------------------------
    # Phase 1 — series
    # ------------------------------------------------------------------

    async def _sync_series(self) -> None:
        started = self.clock()
        rows = await self.source.get_series_list()
        counts = self.result.phases[SyncPhase.SERIES]
        counts.fetched = len(rows)
        written = await write_page(self, SyncPhase.SERIES, Page(series=rows))
        counts.written += written
        counts.unchanged = counts.fetched - counts.written - counts.skipped
        self.series_known.update(r.ticker for r in rows)
        await self.phase_finished(SyncPhase.SERIES, started)

    # ------------------------------------------------------------------
    # Phase 2 — markets walk (Decision 1) with parent resolution (Decision 9)
    # ------------------------------------------------------------------

    async def _walk_markets(self) -> None:
        started = self.clock()
        for status in CATALOG_WALK_FILTERS:
            markets = self.source.iter_markets(
                status=status, mve_filter=KALSHI_MVE_FILTER, limit=MARKETS_PAGE_LIMIT
            )
            async for page in paged(markets, MARKETS_PAGE_LIMIT):
                await self.ingest_markets(SyncPhase.MARKETS, page)
                self.seen.update(m.ticker for m in page)
        await self.phase_finished(SyncPhase.MARKETS, started)

    async def ingest_markets(self, phase: SyncPhase, markets: Sequence[Market]) -> int:
        """Resolve parents, write the page, account counts; returns rows written."""
        counts = self.result.phases[phase]
        counts.fetched += len(markets)
        page = await self._resolve_parents(phase, markets)
        written = await write_page(self, phase, page)
        counts.written += written
        counts.unchanged = counts.fetched - counts.written - counts.skipped
        return written

    async def _resolve_parents(
        self, phase: SyncPhase, markets: Sequence[Market]
    ) -> Page:
        wanted = {m.event_ticker for m in markets}
        known = await self.repository.known_event_tickers(wanted)
        fetched: dict[str, Event] = {}
        for batch in batched(sorted(wanted - known), TICKERS_BATCH_SIZE):
            page = await self.source.get_events(
                tickers=",".join(batch), limit=EVENTS_PAGE_LIMIT
            )
            fetched.update((e.event_ticker, e) for e in page.events)
        for ticker in sorted(wanted - known - fetched.keys()):
            event = await self._fetch_event_singly(phase, ticker)
            if event is not None:
                fetched[ticker] = event
        series, bad_series = await self._resolve_series(phase, fetched.values())
        events = [e for e in fetched.values() if e.series_ticker not in bad_series]
        available = known | {e.event_ticker for e in events}
        writable: list[Market] = []
        for market in markets:
            if market.event_ticker in available:
                writable.append(market)
            else:
                await self.item_error(
                    phase,
                    market.ticker,
                    f"parent event {market.event_ticker} unavailable",
                )
        return Page(series=series, events=events, markets=writable)

    async def _fetch_event_singly(self, phase: SyncPhase, ticker: str) -> Event | None:
        """``GET /events/{ticker}`` for an event the batch lookup omitted."""
        try:
            return await self.source.get_event(ticker)
        except ProviderPermanentError as exc:
            # Decision 9: a parent that cannot be obtained makes its
            # dependents item errors (reported per market below).
            logger.error("%s: event %s unavailable singly: %s", phase, ticker, exc)
            return None

    async def _resolve_series(
        self, phase: SyncPhase, events: Iterable[Event]
    ) -> tuple[list[Series], set[str]]:
        """Series for events whose series is neither in this run's list nor stored."""
        wanted = {e.series_ticker for e in events} - self.series_known
        wanted -= await self.repository.known_series_tickers(wanted)
        rows: list[Series] = []
        unobtainable: set[str] = set()
        for ticker in sorted(wanted):
            try:
                rows.append(await self.source.get_series(ticker))
            except ProviderPermanentError as exc:
                # Decision 9: a parent that cannot be obtained makes its
                # dependents item errors; the run continues.
                unobtainable.add(ticker)
                await self.item_error(phase, ticker, f"series unavailable: {exc}")
            else:
                self.series_known.add(ticker)
        return rows, unobtainable

    # ------------------------------------------------------------------
    # Phase 3 — events refresh (metadata changes on known events)
    # ------------------------------------------------------------------

    async def _refresh_events(self) -> None:
        started = self.clock()
        last = self.state.last_full_sync_at if self.state else None
        if last is not None:
            floor = epoch(last - WINDOW_OVERLAP)
            cursor: str | None = None
            while True:
                page = await self.source.get_events(
                    cursor=cursor, min_updated_ts=floor, limit=EVENTS_PAGE_LIMIT
                )
                await self._ingest_events(page.events)
                if not page.cursor:
                    break
                cursor = page.cursor
        await self.phase_finished(SyncPhase.EVENTS, started)

    async def _ingest_events(self, events: Sequence[Event]) -> None:
        counts = self.result.phases[SyncPhase.EVENTS]
        counts.fetched += len(events)
        series, bad_series = await self._resolve_series(SyncPhase.EVENTS, events)
        writable = [e for e in events if e.series_ticker not in bad_series]
        counts.written += await write_page(
            self, SyncPhase.EVENTS, Page(series=series, events=writable)
        )
        counts.unchanged = counts.fetched - counts.written - counts.skipped
