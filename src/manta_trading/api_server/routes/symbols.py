"""Route handlers for ``GET /api/v1/symbols`` and ``GET /api/v1/symbols/{symbol}``."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from manta_trading.api_server.deps import get_db, get_universe_edges
from manta_trading.api_server.models.responses import (
    GATEWAY_TIMEOUT_RESPONSE,
    AvailableRange,
    SymbolDetail,
    SymbolsResponse,
    SymbolSummary,
)
from manta_trading.api_server.queries import (
    UniverseEdgeCache,
    fetch_symbol_coverage,
    fetch_symbol_head,
    merge_available_ranges,
)
from manta_trading.constants import CycleGranularity, Granularity

router = APIRouter()

_MINUTE_GRANULARITIES: tuple[Granularity, ...] = (
    Granularity.M1,
    Granularity.M5,
    Granularity.M15,
    Granularity.H1,
    Granularity.H4,
)

_DAILY_GRANULARITIES: tuple[Granularity, ...] = (
    Granularity.D1,
    Granularity.W1,
    Granularity.MO1,
    Granularity.Q1,
)

_LIST_ALL_SQL = """
    SELECT symbol, eodhd_exchange, eodhd_type, asset_class,
           NOT delisted_at_eodhd AS active
    FROM instruments
    ORDER BY symbol
"""

_LIST_FILTERED_SQL = """
    SELECT symbol, eodhd_exchange, eodhd_type, asset_class,
           NOT delisted_at_eodhd AS active
    FROM instruments
    WHERE symbol ILIKE %s
    ORDER BY symbol
"""

_INSTRUMENT_SQL = """
    SELECT symbol, eodhd_exchange, eodhd_type, asset_class,
           NOT delisted_at_eodhd AS active
    FROM instruments
    WHERE symbol = %s
"""

_FAMILY_GRANULARITIES: dict[CycleGranularity, tuple[Granularity, ...]] = {
    CycleGranularity.MINUTE: _MINUTE_GRANULARITIES,
    CycleGranularity.DAILY: _DAILY_GRANULARITIES,
}
"""Which response keys each coverage family fans out to (D7).

``available`` advertises one range per *granularity*, but the read produces one
range per *family* — so the mapping is here, dispatching on ``CycleGranularity``
members rather than on ``"minute"``/``"daily"`` literals.
"""


@router.get("/api/v1/symbols", responses=GATEWAY_TIMEOUT_RESPONSE)
async def list_symbols(
    search: str | None = None,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> SymbolsResponse:
    """Return instruments matching an optional prefix search."""
    loop = asyncio.get_running_loop()

    def _query() -> list[SymbolSummary]:
        if search is not None:
            cursor = db.execute(_LIST_FILTERED_SQL, (search + "%",))
        else:
            cursor = db.execute(_LIST_ALL_SQL)
        rows = cursor.fetchall()
        return [
            SymbolSummary(
                symbol=row[0],
                exchange=row[1],
                type=row[2],
                asset_class=row[3],
                active=row[4],
            )
            for row in rows
        ]

    symbols = await loop.run_in_executor(None, _query)
    return SymbolsResponse(symbols=symbols, count=len(symbols))


@router.get("/api/v1/symbols/{symbol}", responses=GATEWAY_TIMEOUT_RESPONSE)
async def get_symbol(
    symbol: str,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
    edges: Annotated[UniverseEdgeCache, Depends(get_universe_edges)] = None,  # type: ignore[assignment]
) -> SymbolDetail:
    """Return full metadata and available data ranges for a single symbol."""
    # `available` is the coverage floor merged with a bounded head probe (D2),
    # not a live MIN/MAX. The response shape is unchanged — only how the ranges
    # are computed — so docs/api/openapi.json must show no diff for this route.
    # Kept as a comment rather than in the docstring for exactly that reason:
    # FastAPI publishes the docstring as the route's public `description`, and
    # internal decision references do not belong in the client-facing spec.
    # See queries.merge_available_ranges for why the COALESCE order is what it is.
    loop = asyncio.get_running_loop()

    def _fetch_instrument() -> tuple[Any, ...] | None:
        cursor = db.execute(_INSTRUMENT_SQL, (symbol,))
        return cursor.fetchone()  # type: ignore[return-value]

    def _fetch_ranges() -> dict[CycleGranularity, tuple[date, date]]:
        """Three statements, sequentially, on one connection (D7).

        Not ``asyncio.gather``: psycopg serializes execution on a connection's
        lock, so dispatching these onto separate executor threads would buy no
        parallelism while obscuring that fact — the pre-187 code did exactly
        that and paid the sum of both queries. ``status.py::get_status``
        documents the same reasoning.
        """
        universe = edges.get(db)
        coverage = fetch_symbol_coverage(db, symbol)
        head = fetch_symbol_head(db, symbol, universe)
        return merge_available_ranges(coverage, head)

    row = await loop.run_in_executor(None, _fetch_instrument)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")

    merged = await loop.run_in_executor(None, _fetch_ranges)

    available: dict[str, AvailableRange] = {}
    for family, (start, end) in merged.items():
        family_range = AvailableRange(start=start, end=end)
        for gran in _FAMILY_GRANULARITIES[family]:
            available[str(gran)] = family_range

    return SymbolDetail(
        symbol=row[0],
        exchange=row[1],
        type=row[2],
        asset_class=row[3],
        active=row[4],
        available=available,
    )
