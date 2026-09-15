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
from fastapi import APIRouter, Depends, Query, Response
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
from manta_trading.api_server.routes.windows import resolve_window
from manta_trading.api_server.serialization import (
    ResponseFormat,
    timeseries_response,
)
from manta_trading.data.kalshi import serve_timeseries as series
from manta_trading.data.kalshi.constants import COLLECTED_CANDLE_PERIOD

router = APIRouter(prefix="/api/v1/kalshi")

_PERIOD_MINUTES = int(COLLECTED_CANDLE_PERIOD)


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
    responses=GATEWAY_TIMEOUT_RESPONSE,
)
async def get_candlesticks(
    ticker: str,
    pool: Annotated[ConnectionPool[psycopg.Connection[Any]], Depends(get_db_pool)],
    max_rows: Annotated[int, Depends(get_max_bars)],
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    fmt: Annotated[ResponseFormat, Query(alias="format")] = "json",
) -> Response:
    """Return this market's candlesticks over the requested window.

    ``start`` and ``end`` are inclusive and optional; omitting both returns
    every stored candle. A bare date means that whole UTC day. The response
    reports whether the market is collected at all and how far its coverage
    reaches, so an empty window is never ambiguous.
    """
    lower, upper = resolve_window(start, end)
    loop = asyncio.get_running_loop()

    def _read() -> _Read[series.CandleRow]:
        # One checkout for seek + count + fetch, released before the model is
        # built and serialized (185 D8a).
        with pool.connection() as conn:
            context = series.market_context(conn, ticker, period=_PERIOD_MINUTES)
            if context is None:
                raise not_found("Market", ticker)
            count = series.count_candles(
                conn, ticker, period=_PERIOD_MINUTES, start=lower, end=upper
            )
            if count > max_rows:
                # Refused by the caller once the connection is back.
                return _Read(context=context, count=count, rows=[])
            rows = series.fetch_candles(
                conn, ticker, period=_PERIOD_MINUTES, start=lower, end=upper
            )
        return _Read(context=context, count=count, rows=rows)

    read = await loop.run_in_executor(None, _read)
    admit_rows(read.count, max_rows, remedy=NARROW_WINDOW)
    response = CandlesResponse.build(read.context, _PERIOD_MINUTES, read.rows)
    return timeseries_response(response, fmt)


@router.get(
    "/markets/{ticker}/trades",
    response_class=Response,
    responses=GATEWAY_TIMEOUT_RESPONSE,
)
async def get_trades(
    ticker: str,
    pool: Annotated[ConnectionPool[psycopg.Connection[Any]], Depends(get_db_pool)],
    max_rows: Annotated[int, Depends(get_max_bars)],
    excluded: Annotated[frozenset[str], Depends(get_kalshi_trades_excluded)],
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    fmt: Annotated[ResponseFormat, Query(alias="format")] = "json",
) -> Response:
    """Return this market's trades over the requested window.

    ``start`` and ``end`` are inclusive and optional; omitting both returns
    every stored trade. The response reports how far the tape reaches and
    whether this market's category is excluded from collection, so an empty
    window is never ambiguous.
    """
    lower, upper = resolve_window(start, end)
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
    return timeseries_response(response, fmt)
