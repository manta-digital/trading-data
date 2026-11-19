"""Route handler for ``GET /api/v1/bars/{symbol}``."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any

import pandas as pd
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import status as http_status
from psycopg_pool import ConnectionPool

from manta_trading.api_server.deps import (
    get_daily_db,
    get_db_pool,
    get_max_bars,
    get_minute_db,
    get_universe_edges,
)
from manta_trading.api_server.latest_bars import (
    MINUTE_GRAINS,
    available_ranges,
    family_of,
    read_latest,
)
from manta_trading.api_server.models.responses import (
    GATEWAY_TIMEOUT_RESPONSE,
    NOT_FOUND_RESPONSE,
    AppliedEquityBarParams,
    BarsResponse,
)
from manta_trading.api_server.queries import symbol_exists
from manta_trading.api_server.routes.bars_admission import check_request
from manta_trading.api_server.routes.windows import (
    window_end_utc,
    window_start_utc,
)
from manta_trading.api_server.serialization import (
    ResponseFormat,
    send_timeseries,
    timeseries_200,
)
from manta_trading.constants import (
    CAGG_BASE_GRANULARITY,
    DEFAULT_PRINT_MODE,
    GRANULARITY_SOURCE,
    Granularity,
    PrintMode,
)
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    assert_cagg_fresh,
)
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

router = APIRouter()

LimitParam = Annotated[
    int | None,
    Query(
        ge=1,
        description=(
            "The N most recent bars at or before end (the latest held when end"
            " is omitted). Replaces start; at most the row ceiling."
        ),
    ),
]

Fetch = Callable[[date, date], pd.DataFrame]


def _fetcher(
    minute_db: TimescaleMinuteDataDB,
    daily_db: TimescaleDailyDataDB,
    symbol: str,
    granularity: Granularity,
    options: tuple[bool, PrintMode],
) -> Fetch:
    """A synchronous read of ``symbol`` over an inclusive date window."""
    adjusted, prints = options

    def fetch(start: date, end: date) -> pd.DataFrame:
        if granularity not in MINUTE_GRAINS:
            return daily_db.get_daily_data(
                symbol, start, end, granularity, adjusted=adjusted
            )
        # M1 queries raw minute_ohlcv (aggregation=None); the other minute
        # granularities are served from pre-aggregated views.
        agg = None if granularity == Granularity.M1 else granularity
        return minute_db.get_minute_data(
            symbol,
            window_start_utc(start),
            window_end_utc(end),
            agg,
            adjusted=adjusted,
            prints=prints,
        )

    return fetch


async def _read(
    fetch: Fetch,
    latest: tuple[Request, str],
    granularity: Granularity,
    span: tuple[date | None, date | None, int | None],
) -> tuple[pd.DataFrame, date | None, date | None]:
    """The bars and the inclusive window read: the request's own, or for
    ``limit`` the bounded latest-N windows (issue #13). Only a ``limit`` read
    looks up the symbol's held range, so only it needs the edge cache."""
    loop = asyncio.get_running_loop()
    start, end, limit = span
    if limit is None:
        assert start is not None and end is not None  # check_request
        return await loop.run_in_executor(None, fetch, start, end), start, end
    request, symbol = latest
    pool, edges = get_db_pool(request), get_universe_edges(request)
    held = (
        await loop.run_in_executor(None, available_ranges, pool, edges, symbol)
    ).get(family_of(granularity))
    window = None
    if held is not None:

        async def fetch_async(lower: date, upper: date) -> pd.DataFrame:
            return await loop.run_in_executor(None, fetch, lower, upper)

        window = await read_latest(fetch_async, held, granularity, limit, end)
    if window is None:
        return _no_bars(), None, end
    return window.bars, window.start, window.end


def _no_bars() -> pd.DataFrame:
    """The empty frame the readers return for a window with no bars."""
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


@router.get(
    "/api/v1/bars/{symbol}",
    response_class=Response,
    responses=timeseries_200(BarsResponse)
    | GATEWAY_TIMEOUT_RESPONSE
    | NOT_FOUND_RESPONSE,
)
async def get_bars(
    request: Request,
    symbol: str,
    granularity: Granularity,
    minute_db: Annotated[TimescaleMinuteDataDB, Depends(get_minute_db)],
    daily_db: Annotated[TimescaleDailyDataDB, Depends(get_daily_db)],
    pool: Annotated[ConnectionPool[psycopg.Connection[Any]], Depends(get_db_pool)],
    max_bars: Annotated[int, Depends(get_max_bars)],
    start: date | None = None,
    end: date | None = None,
    limit: LimitParam = None,
    adjusted: bool = True,
    prints: Annotated[
        PrintMode,
        Query(
            description=(
                "eligible (the default): tape-eligible bars at every granularity. "
                "all: raw prints, served at 1m only."
            )
        ),
    ] = DEFAULT_PRINT_MODE,
    fmt: Annotated[ResponseFormat, Query(alias="format")] = "json",
) -> Response:
    """Return OHLCV bars for ``symbol`` over ``start``..``end``, or the
    ``limit`` most recent at or before ``end``.

    For a cagg-served granularity the response carries ``is_stale``, probed
    against the exact view the bars came from (slice 185 D7). ``M1``/``D1`` read
    raw hypertables, so no probe is issued and ``is_stale`` is always ``False``.

    Requests are admitted before any database work (186 D4): a reversed range,
    an estimate over the ceiling, ``limit`` over it or beside ``start`` is a
    ``422``, not a slow ``500``. ``prints=all`` (raw prints) is served at ``1m``
    only (slice 173).
    """
    span = (start, end, limit)
    check_request(granularity, prints, span, max_bars)
    fetch = _fetcher(minute_db, daily_db, symbol, granularity, (adjusted, prints))
    read = _read(fetch, (request, symbol), granularity, span)

    verdict: FreshnessVerdict | None = None
    if CAGG_BASE_GRANULARITY[granularity] != granularity:
        # Plain gather (no return_exceptions): the probe cannot raise for a
        # granularity FastAPI already validated, and a genuine bug here should
        # fail the request via the global 500 handler (D9). The read and the
        # probe use different connections, so they run truly concurrently.
        loop = asyncio.get_running_loop()
        (df, read_start, read_end), verdict = await asyncio.gather(
            read, loop.run_in_executor(None, _probe_freshness, pool, granularity)
        )
    else:
        df, read_start, read_end = await read

    if df.empty:
        await _raise_if_unknown(pool, symbol)

    applied = AppliedEquityBarParams(
        granularity=granularity,
        start=read_start,
        end=read_end,
        limit=limit,
        adjusted=adjusted,
        prints=prints,
        format=fmt,
    )
    response = BarsResponse.from_dataframe(
        symbol,
        df,
        applied=applied,
        is_stale=verdict is not None and not verdict.is_fresh,
    )
    return await send_timeseries(request, response, fmt)


def _probe_freshness(
    pool: ConnectionPool[psycopg.Connection[Any]], granularity: Granularity
) -> FreshnessVerdict:
    """The serving cagg's freshness.

    The connection is checked out here, not for the whole request: holding one
    across the full request let 8 concurrent bars requests exhaust the pool and
    stall /health. No source_table override: the helper resolves the raw table
    itself from GRANULARITY_SOURCE/CAGG_BASE_GRANULARITY. Probes never raise on
    I/O failure; they return a stale verdict.
    """
    with pool.connection() as conn:
        return assert_cagg_fresh(conn, GRANULARITY_SOURCE[granularity])


async def _raise_if_unknown(
    pool: ConnectionPool[psycopg.Connection[Any]], symbol: str
) -> None:
    """404 means "unknown symbol", nothing else (186 D5); an empty window for
    a known symbol is an empty 200. The lookup runs only for an empty result,
    and the checkout is scoped to the query (185 D8a)."""

    def _symbol_is_known() -> bool:
        with pool.connection() as conn:
            return symbol_exists(conn, symbol)

    loop = asyncio.get_running_loop()
    if not await loop.run_in_executor(None, _symbol_is_known):
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Symbol '{symbol}' not found",
        )
