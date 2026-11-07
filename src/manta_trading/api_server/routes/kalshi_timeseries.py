"""Route handlers for the Kalshi time series under ``/api/v1/kalshi`` (188).

Two routes, one shape: resolve the window, then a single executor call that
seeks the market, counts, and fetches inside one pooled connection; then build
the model and serialize. The checkout is scoped to the reads and released
before serialization (185 D8a), and the over-ceiling refusal is raised after
the executor call returns so no connection is held while unwinding.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime  # noqa: TC003 — FastAPI needs these
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response
from psycopg_pool import ConnectionPool

# ``get_max_bars``: see the note in ``kalshi_catalog.py`` — the setting name
# is bars-specific, the ceiling it carries is not (188 code review F003).
from manta_trading.api_server.admission import (
    NARROW_WINDOW,
    admit_rows,
    not_found,
)
from manta_trading.api_server.deps import (
    get_db_pool,
    get_kalshi_trades_excluded,
    get_max_bars,
)
from manta_trading.api_server.models.kalshi_timeseries import (
    CandlesResponse,
    TradesResponse,
)
from manta_trading.api_server.models.responses import GATEWAY_TIMEOUT_RESPONSE
from manta_trading.api_server.routes.windows import resolve_half_open_window
from manta_trading.api_server.serialization import (
    ResponseFormat,
    send_timeseries,
    timeseries_200,
)
from manta_trading.data.kalshi import serve_candle_periods as periods
from manta_trading.data.kalshi import serve_timeseries as series
from manta_trading.data.kalshi.constants import (
    COLLECTED_CANDLE_PERIOD,
    CandlePeriod,
)

router = APIRouter(prefix="/api/v1/kalshi")

_PERIOD_MINUTES = int(COLLECTED_CANDLE_PERIOD)


def _count_candles(
    conn: psycopg.Connection[Any],
    ticker: str,
    period: int,
    start: datetime | None,
    end: datetime | None,
) -> int:
    """Count served candles: stored rows at the collected period, derived
    periods otherwise."""
    if period == _PERIOD_MINUTES:
        return series.count_candles(conn, ticker, period=period, start=start, end=end)
    return periods.count_period_candles(
        conn,
        ticker,
        stored_period=_PERIOD_MINUTES,
        period=period,
        start=start,
        end=end,
    )


def _fetch_candles(
    conn: psycopg.Connection[Any],
    ticker: str,
    period: int,
    start: datetime | None,
    end: datetime | None,
) -> list[series.CandleRow]:
    if period == _PERIOD_MINUTES:
        return series.fetch_candles(conn, ticker, period=period, start=start, end=end)
    return periods.fetch_period_candles(
        conn,
        ticker,
        stored_period=_PERIOD_MINUTES,
        period=period,
        start=start,
        end=end,
    )


@dataclass(frozen=True)
class _Read[Row]:
    """What one executor call produced: the context, the count, and the rows.

    ``rows`` is empty when the count exceeded the ceiling — the refusal itself
    is raised by the caller, after the connection is back in the pool.
    """

    context: series.MarketContext
    count: int
    rows: list[Row]
    facts: series.TapeFacts | None = None


@router.get(
    "/markets/{ticker}/candlesticks",
    response_class=Response,
    responses=timeseries_200(CandlesResponse) | GATEWAY_TIMEOUT_RESPONSE,
)
async def get_candlesticks(
    request: Request,
    ticker: str,
    pool: Annotated[ConnectionPool[psycopg.Connection[Any]], Depends(get_db_pool)],
    max_rows: Annotated[int, Depends(get_max_bars)],
    period_minutes: Annotated[
        CandlePeriod,
        Query(description="Candle period in minutes: 1, 60 or 1440."),
    ] = COLLECTED_CANDLE_PERIOD,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    fmt: Annotated[ResponseFormat, Query(alias="format")] = "json",
) -> Response:
    """Return this market's candlesticks over the requested window.

    ``period_minutes`` picks the candle size. Only minutes are collected; 1h
    and 1d candles are built from the stored minutes on read, UTC-aligned, with
    ``end_period_ts`` the period end. The newest hour or day is served from the
    minutes stored so far, with no partial flag: any period ending after
    ``complete_through`` is partial.

    The window is half-open, ``[start, end)``, and holds the candles that
    *open* inside it; both bounds are optional and omitting both returns every
    stored candle. A bare date range ``D..E`` means ``[D 00:00, E+1 00:00)``
    UTC. The response reports whether the market is collected at all and how
    far its coverage reaches, so an empty window is never ambiguous.
    """
    lower, upper = resolve_half_open_window(start, end)
    period = int(period_minutes)
    loop = asyncio.get_running_loop()

    def _read() -> _Read[series.CandleRow]:
        # One checkout for seek + count + fetch, released before the model is
        # built and serialized (185 D8a).
        with pool.connection() as conn:
            context = series.market_context(conn, ticker, period=_PERIOD_MINUTES)
            if context is None:
                raise not_found("Market", ticker)
            count = _count_candles(conn, ticker, period, lower, upper)
            if count > max_rows:
                # Refused by the caller once the connection is back.
                return _Read(context=context, count=count, rows=[])
            rows = _fetch_candles(conn, ticker, period, lower, upper)
        return _Read(context=context, count=count, rows=rows)

    read = await loop.run_in_executor(None, _read)
    admit_rows(read.count, max_rows, remedy=NARROW_WINDOW)
    response = CandlesResponse.build(read.context, period, read.rows)
    return await send_timeseries(request, response, fmt)


@router.get(
    "/markets/{ticker}/trades",
    response_class=Response,
    responses=timeseries_200(TradesResponse) | GATEWAY_TIMEOUT_RESPONSE,
)
async def get_trades(
    request: Request,
    ticker: str,
    pool: Annotated[ConnectionPool[psycopg.Connection[Any]], Depends(get_db_pool)],
    max_rows: Annotated[int, Depends(get_max_bars)],
    excluded: Annotated[frozenset[str], Depends(get_kalshi_trades_excluded)],
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    fmt: Annotated[ResponseFormat, Query(alias="format")] = "json",
) -> Response:
    """Return this market's trades over the requested window.

    The window is half-open, ``[start, end)``; both bounds are optional and
    omitting both returns every stored trade. A bare date range ``D..E`` means
    ``[D 00:00, E+1 00:00)`` UTC. The response reports how far the tape reaches and
    whether this market's category is excluded from collection, so an empty
    window is never ambiguous.
    """
    lower, upper = resolve_half_open_window(start, end)
    loop = asyncio.get_running_loop()

    def _read() -> _Read[series.TradeRow]:
        # One checkout for seek + facts + count + fetch (185 D8a).
        with pool.connection() as conn:
            context = series.market_context(conn, ticker, period=_PERIOD_MINUTES)
            if context is None:
                raise not_found("Market", ticker)
            facts = series.tape_facts(conn)
            count = series.count_trades(conn, ticker, start=lower, end=upper)
            if count > max_rows:
                return _Read(context=context, count=count, rows=[], facts=facts)
            rows = series.fetch_trades(conn, ticker, start=lower, end=upper)
        return _Read(context=context, count=count, rows=rows, facts=facts)

    read = await loop.run_in_executor(None, _read)
    admit_rows(read.count, max_rows, remedy=NARROW_WINDOW)
    response = TradesResponse.build(
        ticker,
        read.facts,
        filtered=series.tape_filtered(read.context.series_category, excluded),
        rows=read.rows,
    )
    return await send_timeseries(request, response, fmt)
