"""Phase 4 of the catalog pass — the settled stream in windows (Decision 4).

Windows ``[a, b)`` of ``SETTLED_WINDOW`` are walked oldest-first from the
floor (``--settled-since`` | ``watermark_ts`` | the historical cutoff,
Decision 5) up to the run start; the last window is clamped to the run
start. After a window is *fully walked* — to its last page, whatever its
length — the watermark advances to ``b`` in its own transaction. Each
window's request starts ``WINDOW_OVERLAP`` early because the parameters are
strict at second granularity; the upsert makes the overlap free.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from manta_trading.data.kalshi.constants import (
    KALSHI_MVE_FILTER,
    MARKETS_PAGE_LIMIT,
    SETTLED_WINDOW,
    WINDOW_OVERLAP,
    Surface,
)
from manta_trading.data.kalshi.sync_types import SyncPhase, epoch, paged
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    from manta_trading.data.kalshi.sync import CatalogSync

logger = get_logger(__name__)


async def drain_settled(core: CatalogSync, settled_since: datetime | None) -> None:
    started = core.clock()
    watermark = core.state.watermark_ts if core.state else None
    floor = (
        settled_since
        or watermark
        or (await core.source.get_historical_cutoff()).market_settled_ts
    )
    run_start = core.result.started_at
    window_start = floor
    while window_start < run_start:
        window_end = min(window_start + SETTLED_WINDOW, run_start)
        before = core.result.phases[SyncPhase.SETTLED]
        fetched_before, written_before = before.fetched, before.written
        await _drain_window(core, window_start, window_end)
        # A completed window advances the watermark unless an operator's
        # --settled-since replayed ground already behind it.
        if watermark is None or window_end > watermark:
            async with core.repository.transaction():
                await core.repository.set_watermark(Surface.CATALOG, window_end)
            watermark = window_end
        core.result.windows_completed += 1
        # Under the timer the journal is the only sink: one line per completed
        # window so a multi-window catch-up shows progress (263, Decision 8).
        counts = core.result.phases[SyncPhase.SETTLED]
        logger.info(
            "settled window %s→%s fetched %d written %d (%d windows)",
            window_start.isoformat(),
            window_end.isoformat(),
            counts.fetched - fetched_before,
            counts.written - written_before,
            core.result.windows_completed,
        )
        window_start = window_end
    core.result.watermark_ts = watermark
    await core.phase_finished(
        SyncPhase.SETTLED,
        started,
        captured=core.result.settled_captured,
        windows=core.result.windows_completed,
    )


async def _drain_window(core: CatalogSync, start: datetime, end: datetime) -> None:
    markets = core.source.iter_markets(
        min_settled_ts=epoch(start - WINDOW_OVERLAP),
        max_settled_ts=epoch(end),
        mve_filter=KALSHI_MVE_FILTER,
        limit=MARKETS_PAGE_LIMIT,
    )
    async for page in paged(markets, MARKETS_PAGE_LIMIT):
        await core.ingest_markets(SyncPhase.SETTLED, page)
        new = {m.ticker for m in page} - core.captured
        core.captured.update(new)
        core.result.settled_captured += len(new)
