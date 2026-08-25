"""Row builders and helpers shared by the kalshi integration tests (slice 262).

Rows are the recorded 261 fixtures parsed through the Pydantic models, so
every write uses a real served shape. The recorded pages do not chain
(``markets_page1`` events are not in ``events_page1``, whose series are not
in ``series_list``), so parent rows are synthesized from the child's own
parent ticker — the minimum that satisfies the foreign keys.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psycopg

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi.repository import CatalogRepository, MarketUpsertOutcome

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "kalshi"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def series_rows() -> list[km.Series]:
    return km.SeriesListResponse.model_validate(load_fixture("series_list")).series


def event_rows(page: str = "events_page1") -> list[km.Event]:
    return km.EventsPage.model_validate(load_fixture(page)).events


def market_rows(page: str = "markets_page1") -> list[km.Market]:
    return km.MarketsPage.model_validate(load_fixture(page)).markets


def parent_series(events: Iterable[km.Event]) -> list[km.Series]:
    return [km.Series(ticker=t) for t in sorted({e.series_ticker for e in events})]


def parent_events(markets: Iterable[km.Market]) -> list[km.Event]:
    return [
        km.Event(event_ticker=t, series_ticker=f"{t}-SERIES")
        for t in sorted({m.event_ticker for m in markets})
    ]


async def write_catalog(
    repo: CatalogRepository, markets: list[km.Market]
) -> MarketUpsertOutcome:
    """Markets with synthesized parents, in one transaction."""
    events = parent_events(markets)
    async with repo.transaction():
        await repo.upsert_series(parent_series(events))
        await repo.upsert_events(events)
        outcome = await repo.upsert_markets(markets)
    return outcome


async def column(
    conn: psycopg.AsyncConnection[Any], query: str, *params: object
) -> list[Any]:
    cursor = await conn.execute(query, params)  # type: ignore[arg-type]
    return [row[0] for row in await cursor.fetchall()]
