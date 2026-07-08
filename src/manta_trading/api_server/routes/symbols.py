"""Route handlers for ``GET /api/v1/symbols`` and ``GET /api/v1/symbols/{symbol}``."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import (
    AvailableRange,
    SymbolDetail,
    SymbolSummary,
    SymbolsResponse,
)
from manta_trading.constants import Granularity

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

_MINUTE_RANGE_SQL = """
    SELECT MIN(time_bucket)::date, MAX(time_bucket)::date
    FROM minute_5min_ohlcv
    WHERE symbol = %s
"""

_DAILY_RANGE_SQL = """
    SELECT MIN(time)::date, MAX(time)::date
    FROM daily_ohlcv
    WHERE symbol = %s
"""


@router.get("/api/v1/symbols")
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


@router.get("/api/v1/symbols/{symbol}")
async def get_symbol(
    symbol: str,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> SymbolDetail:
    """Return full metadata and available data ranges for a single symbol."""
    loop = asyncio.get_running_loop()

    def _fetch_instrument() -> tuple[Any, ...] | None:
        cursor = db.execute(_INSTRUMENT_SQL, (symbol,))
        return cursor.fetchone()  # type: ignore[return-value]

    def _fetch_minute_range() -> tuple[Any, Any]:
        cursor = db.execute(_MINUTE_RANGE_SQL, (symbol,))
        row = cursor.fetchone()
        if row is None:
            return (None, None)
        return (row[0], row[1])

    def _fetch_daily_range() -> tuple[Any, Any]:
        cursor = db.execute(_DAILY_RANGE_SQL, (symbol,))
        row = cursor.fetchone()
        if row is None:
            return (None, None)
        return (row[0], row[1])

    row = await loop.run_in_executor(None, _fetch_instrument)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")

    minute_range, daily_range = await asyncio.gather(
        loop.run_in_executor(None, _fetch_minute_range),
        loop.run_in_executor(None, _fetch_daily_range),
    )

    available: dict[str, AvailableRange] = {}
    if minute_range[0] is not None and minute_range[1] is not None:
        ar = AvailableRange(start=minute_range[0], end=minute_range[1])
        for gran in _MINUTE_GRANULARITIES:
            available[str(gran)] = ar
    if daily_range[0] is not None and daily_range[1] is not None:
        ar = AvailableRange(start=daily_range[0], end=daily_range[1])
        for gran in _DAILY_GRANULARITIES:
            available[str(gran)] = ar

    return SymbolDetail(
        symbol=row[0],
        exchange=row[1],
        type=row[2],
        asset_class=row[3],
        active=row[4],
        available=available,
    )
