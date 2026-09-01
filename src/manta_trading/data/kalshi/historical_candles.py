"""The historical phase's candle sub-drain (slice 267, Architecture step 1).

The behind-cutoff set — selected, finalized before the candles cutoff, no
state row — is fetched one market at a time through
``/historical/markets/{ticker}/candlesticks`` in chunks of at most
``CANDLE_SINGLE_MAX_CANDLES`` periods, written and stamped in one
transaction per market, so a stamped market leaves the set. A permanent
error on one market is an item error (Decision 6); a slow market is
warned about and counted (Decision 4).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from manta_trading.data.kalshi.candle_plan import PendingMarket, period_span
from manta_trading.data.kalshi.candle_repository import StateAdvance
from manta_trading.data.kalshi.candle_types import CandleItemError
from manta_trading.data.kalshi.constants import (
    CANDLE_SINGLE_MAX_CANDLES,
    HISTORICAL_CANDLE_MARKETS_PER_PASS,
    HISTORICAL_SLOW_MARKET_SECONDS,
    Surface,
)
from manta_trading.data.kalshi.models import Candlestick
from manta_trading.data.kalshi.sync_types import epoch
from manta_trading.providers.errors import ProviderPermanentError

if TYPE_CHECKING:
    from manta_trading.data.kalshi.historical_sync import HistoricalSync

logger = logging.getLogger(__name__)

#: The phase name item-error lines carry.
PHASE = "historical"


async def drain_candles(core: HistoricalSync, *, floor_reached: bool) -> None:
    """Fetch and stamp behind-cutoff markets until the set, the per-pass
    ceiling, or the cap runs out. ``floor_reached`` lifts the ceiling
    (Decision 9: once the tape is done, the whole budget goes to candles)."""
    result = core.result
    candles_state = await core.catalog.get_sync_state(Surface.CANDLESTICKS)
    if candles_state is None or candles_state.watermark_ts is None:
        logger.info(
            "kalshi historical candles skipped: the candle phase has never run "
            "(no candlesticks sync_state row)"
        )
        return
    cutoff = candles_state.watermark_ts
    limit = None if floor_reached else HISTORICAL_CANDLE_MARKETS_PER_PASS
    pending = await core.candles.pending_behind_cutoff(core.period, cutoff, limit)
    logger.info(
        "kalshi historical candles: cutoff=%s pending=%d (limit %s)",
        cutoff.isoformat(),
        len(pending),
        limit if limit is not None else "none",
    )
    for market in pending:
        if core.cap_reached():
            result.capped = True
            logger.info(
                "kalshi historical cap reached before %s; candles completed=%d",
                market.ticker,
                result.candle_markets_completed,
            )
            break
        await _one_market(core, market)
    result.candle_markets_remaining = await core.candles.count_behind_cutoff(
        core.period, cutoff
    )


async def _one_market(core: HistoricalSync, market: PendingMarket) -> None:
    result = core.result
    started = core.clock()
    try:
        candles = await _fetch(core, market)
    except ProviderPermanentError as exc:
        # Decision 6: one unserved market is an item error, not an abort —
        # no state row is written, so the next firing retries it, and the
        # phase reports PARTIAL.
        logger.error("%s: %s skipped — %s", PHASE, market.ticker, exc)
        result.item_errors.append(CandleItemError(market.ticker, str(exc)))
        return
    close_end = market.close_time + period_span(core.period)
    async with core.candles.transaction():
        written = await core.candles.insert_candles(
            core.period, [(market.ticker, candle) for candle in candles]
        )
        await core.candles.advance_state(
            core.period, [StateAdvance(market.ticker, close_end, market.open_time)]
        )
    result.candles_written += written
    result.candle_markets_completed += 1
    elapsed = (core.clock() - started).total_seconds()
    if elapsed > HISTORICAL_SLOW_MARKET_SECONDS:
        result.slow_markets += 1
        logger.warning(
            "kalshi historical slow market %s: %.1fs for %d candles (threshold %ds; "
            "the compression-pause lever is runbook 100's)",
            market.ticker,
            elapsed,
            written,
            HISTORICAL_SLOW_MARKET_SECONDS,
        )


async def _fetch(core: HistoricalSync, market: PendingMarket) -> list[Candlestick]:
    """``[open_time, close_time + period)`` in chunks under the single-market
    cap, one request per chunk."""
    span = period_span(core.period)
    chunk = span * CANDLE_SINGLE_MAX_CANDLES
    end = market.close_time + span
    start = market.open_time
    candles: list[Candlestick] = []
    while start < end:
        chunk_end = min(start + chunk, end)
        rows = await core.source.get_historical_market_candlesticks(
            market.ticker,
            start_ts=epoch(start),
            end_ts=epoch(chunk_end),
            period_interval=core.period,
        )
        core.result.requests += 1
        core.result.candle_requests += 1
        candles.extend(rows)
        start = chunk_end
    return candles
