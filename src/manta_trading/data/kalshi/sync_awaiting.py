"""Phase 5 of the catalog pass — the awaiting-settlement guarantee (Decision 3).

In one transaction: refresh stored close times, enter every market whose
close has passed and is not finalized, retire every finalized-with-result
market. Then the *vanished* set — awaiting tickers this run neither walked
nor captured from the stream — is looked up directly in ``tickers`` batches
(every markets request carries ``mve_filter=exclude``, Decision 2), what
returns is upserted, retirement runs again, and the batch is marked
checked. Tickers the API omitted are counted ``unreachable`` and stay in
the set: not an error, a fact for ``status`` to report.
"""

from __future__ import annotations

from itertools import batched
from typing import TYPE_CHECKING

from manta_trading.data.kalshi.constants import (
    KALSHI_MVE_FILTER,
    MARKETS_PAGE_LIMIT,
    TICKERS_BATCH_SIZE,
)
from manta_trading.data.kalshi.sync_types import SyncPhase

if TYPE_CHECKING:
    from manta_trading.data.kalshi.sync import CatalogSync


async def reconcile_awaiting(core: CatalogSync) -> None:
    started = core.clock()
    now = core.clock()
    repo = core.repository
    result = core.result
    async with repo.transaction():
        await repo.refresh_awaiting_close_times()
        result.awaiting_entered = await repo.enter_awaiting(now)
        result.awaiting_retired = await repo.retire_awaiting()
    awaiting = await repo.awaiting_tickers()
    vanished = [t for t in awaiting if t not in core.seen and t not in core.captured]
    for batch in batched(vanished, TICKERS_BATCH_SIZE):
        page = await core.source.get_markets(
            tickers=",".join(batch),
            mve_filter=KALSHI_MVE_FILTER,
            limit=MARKETS_PAGE_LIMIT,
        )
        await core.ingest_markets(SyncPhase.AWAITING, page.markets)
        result.awaiting_unreachable += len(batch) - len(page.markets)
    async with repo.transaction():
        result.awaiting_retired += await repo.retire_awaiting()
        result.awaiting_checked = await repo.mark_checked(vanished, now)
    await core.phase_finished(
        SyncPhase.AWAITING,
        started,
        entered=result.awaiting_entered,
        retired=result.awaiting_retired,
        checked=result.awaiting_checked,
        unreachable=result.awaiting_unreachable,
    )
