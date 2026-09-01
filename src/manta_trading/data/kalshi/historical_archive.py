"""The historical phase's archive walk (slice 267, Decision 9).

Catalog before tape: ``GET /historical/markets`` is paged newest-first —
the archive's own order, there is no settlement window to ask for — and
every page goes through 262's ``CatalogSync.ingest_markets`` (parents
resolved, upsert), so no archived trade is classified before its market is
known. The cursor is saved after every page and cleared when the walk is
done, so the cap or an abort resumes it; the historical row's watermark,
seeded only after the walk by the trades step, is the done marker.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from manta_trading.data.kalshi.constants import (
    HISTORICAL_ARCHIVE_STOP_MARGIN,
    HISTORICAL_TRADES_FLOOR,
    KALSHI_MVE_FILTER,
    MARKETS_PAGE_LIMIT,
)
from manta_trading.data.kalshi.events import NullSyncEventSink
from manta_trading.data.kalshi.historical_types import HistoricalCatalogSource
from manta_trading.data.kalshi.models import Market
from manta_trading.data.kalshi.sync import CatalogSync
from manta_trading.data.kalshi.sync_types import SyncPhase
from manta_trading.providers.errors import ProviderPermanentError

if TYPE_CHECKING:
    from manta_trading.data.kalshi.historical_sync import HistoricalSync

logger = logging.getLogger(__name__)

#: Decision 9's stop rule: a market settled before this instant cannot have
#: traded after the floor, so a page of them ends the walk.
ARCHIVE_STOP_BEFORE = HISTORICAL_TRADES_FLOOR - HISTORICAL_ARCHIVE_STOP_MARGIN


async def walk_archive(core: HistoricalSync) -> bool:
    """Run (or resume) the walk; ``True`` when it is done — already or now —
    ``False`` when the cap stopped it with the cursor saved."""
    result = core.result
    state = await core.trades.read_state()
    cursor = await core.trades.read_cursor()
    if cursor is None and state is not None and state.watermark_ts is not None:
        # Done on an earlier firing: the watermark is the done marker.
        result.archive_walked = True
        return True
    adapter = HistoricalCatalogSource(core.source)
    sync = CatalogSync(
        adapter,
        core.catalog,
        sink=NullSyncEventSink(),
        clock=core.clock,
        run_id=result.run_id,
    )
    logger.info(
        "kalshi historical archive walk %s cursor=%s",
        "resuming" if cursor else "starting",
        cursor,
    )
    resuming = cursor is not None
    while True:
        if core.cap_reached():
            result.capped = True
            logger.info(
                "kalshi historical cap reached during the archive walk pages=%d; "
                "cursor saved, the next run resumes it",
                result.archive_pages,
            )
            return False
        try:
            page = await core.source.get_historical_markets(
                cursor=cursor, limit=MARKETS_PAGE_LIMIT, mve_filter=KALSHI_MVE_FILTER
            )
        except ProviderPermanentError as exc:
            # The rejected request still spent budget, on the abort path too.
            result.requests += 1
            if not resuming:
                raise
            # Design *Risks*: a cursor saved on an earlier firing may be
            # rejected; the upserts are idempotent, so the walk restarts from
            # the first page rather than aborting every firing forever.
            result.archive_restarted = True
            logger.warning(
                "kalshi historical archive cursor rejected; restarting the walk "
                "from the first page: %s",
                exc,
            )
            cursor = None
            resuming = False
            async with core.trades.transaction():
                await core.trades.set_cursor(None)
            continue
        resuming = False
        result.requests += 1
        result.archive_pages += 1
        result.archive_markets_fetched += len(page.markets)
        parent_requests = adapter.requests
        written = await sync.ingest_markets(SyncPhase.MARKETS, page.markets)
        result.requests += adapter.requests - parent_requests
        result.archive_markets_written += written
        done = not page.cursor or _settled_before_stop(page.markets)
        cursor = None if done else page.cursor
        async with core.trades.transaction():
            await core.trades.set_cursor(cursor)
        logger.info(
            "kalshi historical archive page %d: markets %d written %d oldest %s",
            result.archive_pages,
            len(page.markets),
            written,
            _oldest(page.markets),
        )
        if done:
            result.archive_walked = True
            logger.info(
                "kalshi historical archive walk done pages=%d markets=%d written=%d "
                "parent item errors=%d",
                result.archive_pages,
                result.archive_markets_fetched,
                result.archive_markets_written,
                len(sync.result.item_errors),
            )
            return True


def _settled_before_stop(markets: Sequence[Market]) -> bool:
    """Every market on the page settled before the stop instant (an empty
    page decides nothing — the cursor does)."""
    return bool(markets) and all(
        m.settlement_ts is not None and m.settlement_ts < ARCHIVE_STOP_BEFORE
        for m in markets
    )


def _oldest(markets: Sequence[Market]) -> str:
    stamps = [m.settlement_ts for m in markets if m.settlement_ts is not None]
    return min(stamps).isoformat() if stamps else "-"
