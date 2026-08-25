"""``CatalogSync`` — the Kalshi catalog pass (slice 262).

No httpx, no typer, no SQL: the core depends on a :class:`CatalogSource`
(what it needs from ``KalshiClient``) and a ``CatalogRepository``. The CLI
and slice 263's pass unit call the same :meth:`CatalogSync.run`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, Unpack

from manta_trading.data.kalshi.client import EventsQuery, MarketsQuery
from manta_trading.data.kalshi.models import (
    Event,
    EventsPage,
    HistoricalCutoff,
    Market,
    MarketsPage,
    Series,
)


class CatalogSource(Protocol):
    """The six client calls the sync core uses (design: *CatalogSource protocol*).

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

    async def get_historical_cutoff(self) -> HistoricalCutoff: ...


__all__ = ["CatalogSource", "Event"]
