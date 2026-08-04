"""Route handler for ``GET /api/v1/bars/{symbol}``."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Annotated, Any, Literal

import msgpack
import orjson
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from psycopg_pool import ConnectionPool

from manta_trading.api_server.deps import (
    get_daily_db,
    get_db_pool,
    get_max_bars,
    get_minute_db,
)
from manta_trading.api_server.models.responses import (
    GATEWAY_TIMEOUT_RESPONSE,
    BarsResponse,
)
from manta_trading.api_server.queries import symbol_exists
from manta_trading.constants import (
    BARS_PER_TRADING_DAY,
    CAGG_BASE_GRANULARITY,
    GRANULARITY_SOURCE,
    TRADING_DAYS_PER_CALENDAR_DAY,
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


def _window_start_utc(d: date) -> datetime:
    """Midnight UTC on ``d`` — the inclusive lower bound of the window."""
    return datetime.combine(d, time.min, tzinfo=UTC)


def _window_end_utc(d: date) -> datetime:
    """Last instant of ``d`` in UTC — the inclusive upper bound of the window.

    ``end`` is inclusive at every granularity. The daily path gets this for
    free by passing dates straight to a ``time <= %s`` predicate; the minute
    path converts to a timestamp first, and converting ``end`` to *midnight*
    made the bound effectively exclusive — a Mon–Fri ``1m`` request returned
    Mon–Thu, silently, with nothing in the response to say so. Measured on prod
    2026-08-04: 2,975 bars ending 06-13 23:59 for a window ending 06-14.
    """
    return datetime.combine(d, time.max, tzinfo=UTC)


def _bars_per_calendar_day(granularity: Granularity) -> float:
    """Bars a dense symbol yields per calendar day at this granularity."""
    return BARS_PER_TRADING_DAY[granularity] * TRADING_DAYS_PER_CALENDAR_DAY


def _estimate_bars(granularity: Granularity, start: date, end: date) -> float:
    """Estimate the bars an inclusive ``[start, end]`` window could contain.

    Computed from the request alone — no database work — so a rejection costs
    one comparison (186 D4). It is an admission policy on the *window*, not a
    promise about ``count``: a sparse symbol over an admitted span returns far
    fewer bars.
    """
    span_days = (end - start).days + 1
    return span_days * _bars_per_calendar_day(granularity)


def _max_span_days(granularity: Granularity, ceiling: int) -> int:
    """Largest inclusive window this granularity can request under ``ceiling``.

    Derived from the live ceiling so an operator override never yields a
    message that contradicts the limit actually enforced.
    """
    return int(ceiling / _bars_per_calendar_day(granularity))


def _admit_range(
    granularity: Granularity, start: date, end: date, ceiling: int
) -> None:
    """Reject a window that cannot be served, before any DB work (186 D4).

    Raises:
        HTTPException: 422 for a reversed range, and 422 when the estimated bar
            count exceeds ``ceiling``. Both messages carry what the caller needs
            to fix the request; neither number is a literal.
    """
    if start > end:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"start ({start.isoformat()}) is after end ({end.isoformat()}); "
                "the requested range is empty"
            ),
        )

    estimate = _estimate_bars(granularity, start, end)
    if estimate > ceiling:
        max_days = _max_span_days(granularity, ceiling)
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"requested range spans about {estimate:,.0f} {granularity.value} "
                f"bars, over the {ceiling:,} bar limit; at {granularity.value} "
                f"request at most {max_days:,} days per call, or use a coarser "
                "granularity"
            ),
        )


@router.get(
    "/api/v1/bars/{symbol}",
    response_class=Response,
    responses=GATEWAY_TIMEOUT_RESPONSE,
)
async def get_bars(
    symbol: str,
    granularity: Granularity,
    start: date,
    end: date,
    minute_db: Annotated[TimescaleMinuteDataDB, Depends(get_minute_db)],
    daily_db: Annotated[TimescaleDailyDataDB, Depends(get_daily_db)],
    pool: Annotated[ConnectionPool[psycopg.Connection[Any]], Depends(get_db_pool)],
    max_bars: Annotated[int, Depends(get_max_bars)],
    adjusted: bool = True,
    fmt: Annotated[Literal["json", "msgpack"], Query(alias="format")] = "json",
) -> Response:
    """Return OHLCV bars for ``symbol`` over the requested date range.

    For a cagg-served granularity the response carries ``is_stale``, probed
    against the exact view the bars came from (slice 185 D7). ``M1``/``D1`` read
    raw hypertables, so no probe is issued and ``is_stale`` is always ``False``.

    Windows are admitted before any database work (186 D4): a reversed range or
    one whose estimated bar count exceeds the configured ceiling is a ``422``,
    not a slow ``500``.
    """
    _admit_range(granularity, start, end, max_bars)

    loop = asyncio.get_running_loop()

    def _fetch_minute() -> pd.DataFrame:
        # M1 queries raw minute_ohlcv (aggregation=None); the other minute
        # granularities are served from pre-aggregated views.
        agg = None if granularity == Granularity.M1 else granularity
        return minute_db.get_minute_data(
            symbol,
            _window_start_utc(start),
            _window_end_utc(end),
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
        # 404 means "unknown symbol", nothing else (186 D5). A weekend and a
        # typo used to be indistinguishable. The lookup runs only here, so a
        # normal response pays nothing for it, and the checkout is scoped to
        # the query (185 D8a) rather than held across serialization.
        def _symbol_is_known() -> bool:
            with pool.connection() as conn:
                return symbol_exists(conn, symbol)

        if not await loop.run_in_executor(None, _symbol_is_known):
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Symbol '{symbol}' not found",
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
