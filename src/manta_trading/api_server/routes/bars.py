"""Route handler for ``GET /api/v1/bars/{symbol}``."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from typing import Annotated, Literal

import msgpack
import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from manta_trading.api_server.deps import get_daily_db, get_minute_db
from manta_trading.api_server.models.responses import BarsResponse
from manta_trading.constants import Granularity
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

router = APIRouter()

_MINUTE_GRAINS: frozenset[Granularity] = frozenset(
    {Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4}
)


def _date_to_utc_datetime(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


@router.get("/api/v1/bars/{symbol}", response_class=Response)
async def get_bars(
    symbol: str,
    granularity: Granularity,
    start: date,
    end: date,
    minute_db: Annotated[TimescaleMinuteDataDB, Depends(get_minute_db)],
    daily_db: Annotated[TimescaleDailyDataDB, Depends(get_daily_db)],
    adjusted: bool = True,
    fmt: Annotated[Literal["json", "msgpack"], Query(alias="format")] = "json",
) -> Response:
    """Return OHLCV bars for ``symbol`` over the requested date range."""
    loop = asyncio.get_running_loop()

    if granularity in _MINUTE_GRAINS:
        start_dt = _date_to_utc_datetime(start)
        end_dt = _date_to_utc_datetime(end)
        # M1 queries raw minute_ohlcv (aggregation=None); others use pre-aggregated views
        agg = None if granularity == Granularity.M1 else granularity
        df = await loop.run_in_executor(
            None,
            lambda: minute_db.get_minute_data(
                symbol,
                start_dt,
                end_dt,
                agg,
                adjusted=adjusted,
            ),
        )
    else:
        df = await loop.run_in_executor(
            None,
            lambda: daily_db.get_daily_data(
                symbol,
                start,
                end,
                granularity,
                adjusted=adjusted,
            ),
        )

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol '{symbol}' not found or no data in range",
        )

    response = BarsResponse.from_dataframe(symbol, granularity, adjusted, df)

    if fmt == "msgpack":
        return Response(
            content=msgpack.packb(response.model_dump(), default=str),
            media_type="application/x-msgpack",
        )
    return Response(
        content=orjson.dumps(response.model_dump()),
        media_type="application/json",
    )
