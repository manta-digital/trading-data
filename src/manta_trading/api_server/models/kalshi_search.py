"""Pydantic response models for ``GET /api/v1/kalshi/search`` (#63)."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic needs the runtime type
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from manta_trading.data.kalshi.serve_search import SearchHits


class SeriesHit(BaseModel):
    """A matching series: the top of the hierarchy."""

    ticker: str
    title: str | None
    category: str | None


class EventHit(BaseModel):
    """A matching event and the series it belongs to."""

    event_ticker: str
    series_ticker: str
    title: str | None
    sub_title: str | None
    strike_date: datetime | None


class MarketHit(BaseModel):
    """A matching open market and the event and series above it."""

    ticker: str
    event_ticker: str
    series_ticker: str
    title: str | None
    yes_sub_title: str | None
    no_sub_title: str | None
    close_time: datetime


class SearchResponse(BaseModel):
    """Ranked hits, grouped series, then events, then markets.

    Each group holds at most ``limit`` hits, best first. A group is empty, not
    absent, when nothing at that level matched.
    """

    query: str
    series: list[SeriesHit]
    events: list[EventHit]
    markets: list[MarketHit]

    @classmethod
    def from_hits(cls, query: str, hits: SearchHits) -> SearchResponse:
        return cls(
            query=query,
            series=[SeriesHit(**vars(h)) for h in hits.series],
            events=[EventHit(**vars(h)) for h in hits.events],
            markets=[MarketHit(**vars(h)) for h in hits.markets],
        )
