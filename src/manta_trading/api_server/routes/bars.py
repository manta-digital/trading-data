"""Route handler for ``GET /api/v1/bars/{symbol}``."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Annotated, Any, Literal

import msgpack
import orjson
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg_pool import ConnectionPool

from manta_trading.api_server.deps import get_daily_db, get_db_pool, get_minute_db
from manta_trading.api_server.models.responses import BarsResponse
from manta_trading.constants import (
    CAGG_BASE_GRANULARITY,
    GRANULARITY_SOURCE,
    Granularity,
)
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    assert_cagg_fresh,
)
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

if TYPE_CHECKING:
    import pandas as pd

router = APIRouter()

_MINUTE_GRAINS: frozenset[Granularity] = frozenset(
    {Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4}
)


def _date_to_utc_datetime(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


@router.get("/api/v1/bars/{symbol}", response_class=Response)
async def get_bars(
    symbol: str,
    granularity: Granularity,
    start: date,
    end: date,
    minute_db: Annotated[TimescaleMinuteDataDB, Depends(get_minute_db)],
    daily_db: Annotated[TimescaleDailyDataDB, Depends(get_daily_db)],
    pool: Annotated[ConnectionPool[psycopg.Connection[Any]], Depends(get_db_pool)],
    adjusted: bool = True,
    fmt: Annotated[Literal["json", "msgpack"], Query(alias="format")] = "json",
) -> Response:
    """Return OHLCV bars for ``symbol`` over the requested date range.

    For a cagg-served granularity the response carries ``is_stale``, probed
    against the exact view the bars came from (slice 185 D7). ``M1``/``D1`` read
    raw hypertables, so no probe is issued and ``is_stale`` is always ``False``.
    """
    loop = asyncio.get_running_loop()

    def _fetch_minute() -> pd.DataFrame:
        # M1 queries raw minute_ohlcv (aggregation=None); the other minute
        # granularities are served from pre-aggregated views.
        agg = None if granularity == Granularity.M1 else granularity
        return minute_db.get_minute_data(
            symbol,
            _date_to_utc_datetime(start),
            _date_to_utc_datetime(end),
            agg,
            adjusted=adjusted,
        )

    def _fetch_daily() -> pd.DataFrame:
        return daily_db.get_daily_data(
            symbol,
            start,
            end,
            granularity,
            adjusted=adjusted,
        )

    def _probe_freshness() -> FreshnessVerdict:
        # The connection is checked out here, not for the whole request: this
        # is the only work in get_bars that needs one, and for M1/D1 this
        # function never runs at all. Holding one across the full request let
        # 8 concurrent bars requests exhaust the pool and stall /health.
        #
        # No source_table override: the helper resolves the raw table itself
        # from GRANULARITY_SOURCE/CAGG_BASE_GRANULARITY — the seam it was built
        # for. Probes never raise on I/O failure; they return a stale verdict.
        with pool.connection() as conn:
            return assert_cagg_fresh(conn, GRANULARITY_SOURCE[granularity])

    fetch_bars = _fetch_minute if granularity in _MINUTE_GRAINS else _fetch_daily

    verdict: FreshnessVerdict | None = None
    if CAGG_BASE_GRANULARITY[granularity] != granularity:
        # Plain gather (no return_exceptions): the probe cannot raise for a
        # granularity FastAPI already validated, and a genuine bug here should
        # fail the request via the global 500 handler (D9). The two branches
        # use different connections, so they run truly concurrently.
        df, verdict = await asyncio.gather(
            loop.run_in_executor(None, fetch_bars),
            loop.run_in_executor(None, _probe_freshness),
        )
    else:
        df = await loop.run_in_executor(None, fetch_bars)

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol '{symbol}' not found or no data in range",
        )

    response = BarsResponse.from_dataframe(
        symbol,
        granularity,
        adjusted,
        df,
        is_stale=verdict is not None and not verdict.is_fresh,
    )

    if fmt == "msgpack":
        return Response(
            content=msgpack.packb(response.model_dump(), default=str),
            media_type="application/x-msgpack",
        )
    return Response(
        content=orjson.dumps(response.model_dump()),
        media_type="application/json",
    )
