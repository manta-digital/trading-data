"""Historical-phase value types and adapters (slice 267).

``HistoricalSource`` is what the core needs from the client; the two
adapters are Decision 9's and Decision 5's — they let 262's
``CatalogSync.ingest_markets`` and 265's ``TradeSync.drain`` run unchanged
over the ``/historical/*`` endpoints; ``HistoricalResult`` is what one phase
reports; ``classify_historical`` how its outcome is decided (Decision 6).
Nothing here imports the client or a repository.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Unpack
from uuid import UUID

import psycopg

from manta_trading.data.kalshi.candle_types import CandleItemError
from manta_trading.data.kalshi.client import EventsQuery, MarketsQuery
from manta_trading.data.kalshi.constants import CandlePeriod
from manta_trading.data.kalshi.models import (
    Candlestick,
    Event,
    EventsPage,
    HistoricalCutoff,
    Market,
    MarketsPage,
    Series,
    TradesPage,
)
from manta_trading.data.kalshi.sync_types import SyncOutcome, classify_outcome, iso_utc
from manta_trading.providers.errors import ProviderError


class HistoricalSource(Protocol):
    """The client calls the historical core makes: the three archive
    endpoints plus the parent lookups ``ingest_markets`` needs.
    ``KalshiClient`` satisfies it structurally; tests substitute a fake."""

    async def get_historical_markets(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        mve_filter: str | None = None,
    ) -> MarketsPage: ...

    async def get_historical_trades(
        self, *, cursor: str | None = None, min_ts: int, max_ts: int, limit: int
    ) -> TradesPage: ...

    async def get_historical_market_candlesticks(
        self,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: CandlePeriod,
    ) -> list[Candlestick]: ...

    async def get_events(
        self, *, cursor: str | None = None, **query: Unpack[EventsQuery]
    ) -> EventsPage: ...

    async def get_event(self, event_ticker: str) -> Event: ...

    async def get_series(self, series_ticker: str) -> Series: ...


class HistoricalCatalogSource:
    """Decision 9's adapter: a ``sync.CatalogSource`` whose markets come from
    the archive and whose event/series calls pass through unchanged.

    It also **counts every request it forwards** (``requests``): the parent
    lookups ``ingest_markets`` makes draw on the phase's one cap, and the
    core reads the counter after each page. The two calls the walk never
    makes raise rather than pretend.
    """

    def __init__(self, source: HistoricalSource) -> None:
        self._source = source
        self.requests = 0

    async def get_markets(
        self, *, cursor: str | None = None, **query: Unpack[MarketsQuery]
    ) -> MarketsPage:
        self.requests += 1
        return await self._source.get_historical_markets(
            cursor=cursor, limit=query.get("limit"), mve_filter=query.get("mve_filter")
        )

    async def iter_markets(
        self, **query: Unpack[MarketsQuery]
    ) -> AsyncIterator[Market]:
        cursor: str | None = None
        while True:
            page = await self.get_markets(cursor=cursor, **query)
            for market in page.markets:
                yield market
            if not page.cursor:
                return
            cursor = page.cursor

    async def get_events(
        self, *, cursor: str | None = None, **query: Unpack[EventsQuery]
    ) -> EventsPage:
        self.requests += 1
        return await self._source.get_events(cursor=cursor, **query)

    async def get_event(self, event_ticker: str) -> Event:
        self.requests += 1
        return await self._source.get_event(event_ticker)

    async def get_series(self, series_ticker: str) -> Series:
        self.requests += 1
        return await self._source.get_series(series_ticker)

    async def get_series_list(self) -> list[Series]:
        raise NotImplementedError(
            "the archive walk ingests market pages only; it never lists series"
        )

    async def get_historical_cutoff(self) -> HistoricalCutoff:
        raise NotImplementedError(
            "the archive walk has no cutoff to read; its bound is the floor constant"
        )


class HistoricalTradeSource:
    """Decision 5's adapter: ``TradeSource.get_trades`` over
    ``/historical/trades`` with the same arguments — what lets ``TradeSync``
    walk the archived tape unchanged."""

    def __init__(self, source: HistoricalSource) -> None:
        self._source = source

    async def get_trades(
        self, *, cursor: str | None = None, min_ts: int, max_ts: int, limit: int
    ) -> TradesPage:
        return await self._source.get_historical_trades(
            cursor=cursor, min_ts=min_ts, max_ts=max_ts, limit=limit
        )

    async def get_historical_cutoff(self) -> HistoricalCutoff:
        raise NotImplementedError(
            "the backward walk never reads the cutoff; it descends from the "
            "live floor to HISTORICAL_TRADES_FLOOR"
        )


@dataclass
class HistoricalResult:
    """What one historical phase did — JSON-serializable through
    :meth:`to_dict`. The trades figures are copied from the inner
    ``TradeResult`` after ``drain``, never recomputed."""

    run_id: UUID
    started_at: datetime
    cap: int
    floor: datetime
    requests: int = 0
    capped: bool = False
    # Archive walk (Decision 9)
    archive_walked: bool = False
    archive_pages: int = 0
    archive_markets_fetched: int = 0
    archive_markets_written: int = 0
    archive_restarted: bool = False
    # Candles sub-drain
    candle_markets_completed: int = 0
    candle_requests: int = 0
    candles_written: int = 0
    candle_markets_remaining: int = 0
    slow_markets: int = 0
    item_errors: list[CandleItemError] = field(default_factory=list)
    # Trades sub-drain
    trades_row_missing: bool = False
    watermark_before: datetime | None = None
    watermark_after: datetime | None = None
    floor_reached: bool = False
    windows_completed: int = 0
    trades_fetched: int = 0
    trades_written: int = 0
    unknown_market: int = 0
    excluded_by_rule: int = 0
    duplicates: int = 0
    unknown_prefixes: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None

    def counts(self) -> dict[str, int]:
        """The integer counts the ``phase_finished`` event carries."""
        return {
            "requests": self.requests,
            "capped": int(self.capped),
            "archive_walked": int(self.archive_walked),
            "archive_pages": self.archive_pages,
            "archive_markets_written": self.archive_markets_written,
            "candle_markets_completed": self.candle_markets_completed,
            "candle_requests": self.candle_requests,
            "candles_written": self.candles_written,
            "candle_markets_remaining": self.candle_markets_remaining,
            "slow_markets": self.slow_markets,
            "item_errors": len(self.item_errors),
            "floor_reached": int(self.floor_reached),
            "windows_completed": self.windows_completed,
            "trades_fetched": self.trades_fetched,
            "trades_written": self.trades_written,
            "unknown_market": self.unknown_market,
            "excluded_by_rule": self.excluded_by_rule,
            "duplicates": self.duplicates,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "started_at": self.started_at.isoformat(),
            "cap": self.cap,
            "requests": self.requests,
            "capped": self.capped,
            "archive": {
                "walked": self.archive_walked,
                "pages": self.archive_pages,
                "markets_fetched": self.archive_markets_fetched,
                "markets_written": self.archive_markets_written,
                "restarted": self.archive_restarted,
            },
            "candles": {
                "markets_completed": self.candle_markets_completed,
                "requests": self.candle_requests,
                "candles_written": self.candles_written,
                "markets_remaining": self.candle_markets_remaining,
                "slow_markets": self.slow_markets,
            },
            "item_errors": [e.to_dict() for e in self.item_errors],
            "trades_row_missing": self.trades_row_missing,
            "floor": iso_utc(self.floor),
            "watermark": {
                "before": iso_utc(self.watermark_before),
                "after": iso_utc(self.watermark_after),
            },
            "floor_reached": self.floor_reached,
            "windows_completed": self.windows_completed,
            "trades_fetched": self.trades_fetched,
            "trades_written": self.trades_written,
            "unknown_market": self.unknown_market,
            "excluded_by_rule": self.excluded_by_rule,
            "duplicates": self.duplicates,
            "unknown_prefixes": dict(self.unknown_prefixes),
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def classify_historical(
    result: HistoricalResult, exc: ProviderError | psycopg.OperationalError | None
) -> SyncOutcome:
    """The shared rule (``sync_types.classify_outcome``): ``PARTIAL`` when a
    market was skipped on a permanent error and nothing aborted (Decision
    6) — ``classify_candles``' shape."""
    return classify_outcome(bool(result.item_errors), exc)
