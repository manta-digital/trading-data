"""Futures tick-bar routes: bars of N trades, by contract or by product (308 D4).

Both read through ``read_series`` with the tick tier's plan (the helpers of
``futures_series``), the trade-scan admission as its ``admit`` hook (D5) and
``read_tick_bars`` as the window reader. Errors map as on every futures route
(``tick_errors``), plus three of this route's own: the trade limit (``422``), a
stored trade the cutter refuses (logged, ``500``, D1) and a client that left
mid-read (bodiless ``499``, D10).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette import status as http_status

from manta_trading.api_server.admission import NARROW_TICK_BARS, over_ceiling
from manta_trading.api_server.deps import get_max_tick_bar_trades
from manta_trading.api_server.futures_read import (
    PlanFor,
    RowCeilingExceeded,
    read_series,
)
from manta_trading.api_server.models.futures_tick_bars import FuturesTickBarsResponse
from manta_trading.api_server.models.responses import (
    GATEWAY_TIMEOUT_RESPONSE,
    TICK_UNAVAILABLE_RESPONSE,
)
from manta_trading.api_server.routes.futures_params import (
    EndParam,
    FromParam,
    RuleParam,
    StartParam,
    TicksPerBarParam,
    UntilParam,
    instant_bounds,
)
from manta_trading.api_server.routes.futures_series import (
    Format,
    MaxRows,
    TickConn,
    contract_plan_for,
    product_plan_for,
)
from manta_trading.api_server.serialization import (
    ResponseFormat,
    send_timeseries,
    timeseries_200,
)
from manta_trading.api_server.tick_access import tick_errors
from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_bar_reads import (
    ReadAbandoned,
    TickBarWindowError,
    TradeScanExceeded,
    read_tick_bars,
    trade_admission,
)
from manta_trading.data.tick.tick_bars import TickBarInputError
from manta_trading.data.tick.tick_reads import Row

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/futures", tags=["futures"])

MaxTrades = Annotated[int, Depends(get_max_tick_bar_trades)]
#: Not an IANA code: the de facto "client closed request". Nobody receives it.
CLIENT_CLOSED_REQUEST = 499
_RESPONSES = (
    timeseries_200(FuturesTickBarsResponse)
    | TICK_UNAVAILABLE_RESPONSE
    | GATEWAY_TIMEOUT_RESPONSE
)


async def _read(
    request: Request,
    conn: Conn,
    plan_for: PlanFor,
    ticks_per_bar: int,
    limits: tuple[int, int],
    bounds: tuple[int | None, int | None],
) -> tuple[GetPlan, list[Row]]:
    """Admit, cut and collect under ``limits`` = (row ceiling, trade limit);
    every failure mapped once. Windows go to the reader unclipped (D3)."""
    max_rows, max_trades = limits
    read = partial(
        read_tick_bars,
        ticks_per_bar=ticks_per_bar,
        bounds=bounds,
        stop=request.is_disconnected,
    )
    admit = trade_admission(bounds, max_trades)
    try:
        with tick_errors(request.app):
            return await read_series(conn, plan_for, read, max_rows, admit=admit)
    except RowCeilingExceeded:
        raise over_ceiling(max_rows, remedy=NARROW_TICK_BARS) from None
    except TradeScanExceeded as exc:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from None
    except TickBarWindowError as exc:
        raise _uncuttable(exc) from exc


def _uncuttable(exc: TickBarWindowError) -> HTTPException:
    """Log the window and the refused keys (D1); the stable ``500``."""
    cause = exc.__cause__
    keys = cause.keys if isinstance(cause, TickBarInputError) else None
    _logger.exception(
        "tick bars: stored ticks for session %s of %s (instrument %s) cannot be"
        " cut into bars; previous and current keys %s",
        exc.session_date,
        exc.raw_symbol,
        exc.instrument_id,
        keys,
    )
    return HTTPException(
        http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        f"stored ticks for session {exc.session_date} of {exc.raw_symbol} cannot"
        " be cut into bars; see the server log",
    )


async def _tick_bars(
    request: Request,
    conn: Conn,
    plan_for: PlanFor,
    query: tuple[int, datetime | None, datetime | None],
    limits: tuple[int, int],
    fmt: ResponseFormat,
) -> Response:
    """``query`` is ``(ticks_per_bar, from, until)``."""
    ticks_per_bar, from_, until = query
    bounds = instant_bounds(from_, until)
    try:
        plan, rows = await _read(
            request, conn, plan_for, ticks_per_bar, limits, bounds.ns()
        )
    except ReadAbandoned:
        # The client disconnected mid-read: nobody is left to answer, so this is
        # not an error to log (D10); the snapshot already rolled back.
        return Response(status_code=CLIENT_CLOSED_REQUEST)
    build = partial(
        FuturesTickBarsResponse.from_plan,
        plan,
        ticks_per_bar,
        rows,
        (bounds.lower, bounds.upper),
    )
    model = await asyncio.get_running_loop().run_in_executor(None, build)
    return await send_timeseries(request, model, fmt)


@router.get(
    "/contracts/{symbol}/tick-bars", response_class=Response, responses=_RESPONSES
)
async def contract_tick_bars(
    request: Request,
    symbol: str,
    conn: TickConn,
    max_rows: MaxRows,
    max_trades: MaxTrades,
    ticks_per_bar: TicksPerBarParam,
    start: StartParam = None,
    end: EndParam = None,
    from_: FromParam = None,
    until: UntilParam = None,
    fmt: Format = "json",
) -> Response:
    """One contract's bars of ``ticks_per_bar`` trades over ``[start, end)``
    sessions, those opened in ``[from, until)``. Refused past the trade limit
    or the row ceiling."""
    plan_for = contract_plan_for(request, symbol, (start, end), SchemaFamily.TICK_TIER)
    query = (ticks_per_bar, from_, until)
    return await _tick_bars(request, conn, plan_for, query, (max_rows, max_trades), fmt)


@router.get(
    "/products/{product}/tick-bars", response_class=Response, responses=_RESPONSES
)
async def product_tick_bars(
    request: Request,
    product: str,
    conn: TickConn,
    max_rows: MaxRows,
    max_trades: MaxTrades,
    ticks_per_bar: TicksPerBarParam,
    rule: RuleParam = None,
    start: StartParam = None,
    end: EndParam = None,
    from_: FromParam = None,
    until: UntilParam = None,
    fmt: Format = "json",
) -> Response:
    """A product's tick bars under ``rule``: each session from the contract the
    rule names, the count restarting at every session open."""
    plan_for = product_plan_for(
        request, product, rule, (start, end), SchemaFamily.TICK_TIER
    )
    query = (ticks_per_bar, from_, until)
    return await _tick_bars(request, conn, plan_for, query, (max_rows, max_trades), fmt)
