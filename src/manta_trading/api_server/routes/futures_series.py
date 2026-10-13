"""Futures time-series routes: ticks and bars, by contract or by product
(slice 231, TD1, TD3–TD6).

All four go through :func:`_serve`: one snapshot read (``read_series``) under
the row ceiling, the one error mapping (``tick_errors``), then the model and
``timeseries_response`` (JSON or msgpack). ``start``/``end`` are session dates
with ``end`` exclusive; ticks also take half-open ``[from, until)`` instants.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from manta_trading.api_server.admission import (
    NARROW_TICKS,
    NARROW_WINDOW,
    over_ceiling,
)
from manta_trading.api_server.deps import get_max_bars
from manta_trading.api_server.futures_read import (
    PlanFor,
    ReadWindow,
    RowCeilingExceeded,
    read_series,
)
from manta_trading.api_server.models.futures import (
    FuturesBarsResponse,
    FuturesTicksResponse,
)
from manta_trading.api_server.models.responses import (
    GATEWAY_TIMEOUT_RESPONSE,
    TICK_UNAVAILABLE_RESPONSE,
)
from manta_trading.api_server.routes.futures_params import (
    EndParam,
    FromParam,
    RuleParam,
    StartParam,
    TickBarGranularity,
    UntilParam,
    instant_bounds,
    parse_rule,
    product_entry,
)
from manta_trading.api_server.serialization import (
    ResponseFormat,
    timeseries_200,
    timeseries_response,
)
from manta_trading.api_server.tick_access import tick_connection, tick_errors
from manta_trading.constants import Granularity
from manta_trading.data.tick.get_plan import GetPlan, contract_plan, product_plan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import (
    OnBatch,
    Row,
    TickWindow,
    read_bars,
    read_ticks,
)

router = APIRouter(prefix="/api/v1/futures", tags=["futures"])

TickConn = Annotated[Conn, Depends(tick_connection)]
MaxRows = Annotated[int, Depends(get_max_bars)]
Format = Annotated[ResponseFormat, Query(alias="format")]
GranularityParam = Annotated[
    TickBarGranularity,
    Query(description="Bar size; 1d is the whole session."),
]
_TICKS_RESPONSES = (
    timeseries_200(FuturesTicksResponse)
    | TICK_UNAVAILABLE_RESPONSE
    | GATEWAY_TIMEOUT_RESPONSE
)
_BARS_RESPONSES = (
    timeseries_200(FuturesBarsResponse)
    | TICK_UNAVAILABLE_RESPONSE
    | GATEWAY_TIMEOUT_RESPONSE
)


async def _serve(
    request: Request,
    conn: Conn,
    plan_for: PlanFor,
    read: ReadWindow,
    limit: tuple[int, str],
    bounds: tuple[int | None, int | None] = (None, None),
) -> tuple[GetPlan, list[Row]]:
    """Read under ``limit`` = (ceiling, remedy); every failure mapped once."""
    ceiling, remedy = limit
    try:
        with tick_errors(request.app):
            return await read_series(conn, plan_for, read, ceiling, bounds)
    except RowCeilingExceeded:
        raise over_ceiling(ceiling, remedy=remedy) from None


def _contract_plan(
    request: Request, symbol: str, start: date | None, end: date | None
) -> PlanFor:
    calendar_url = request.app.state.calendar_url

    async def plan(conn: Conn) -> GetPlan:
        return await contract_plan(conn, calendar_url, symbol, start, end)

    return plan


def _product_plan(
    request: Request,
    product: str,
    rule: str | None,
    span: tuple[date | None, date | None],
) -> PlanFor:
    """Resolves the product (404) and the rule (422) before any read."""
    entry, parsed = product_entry(product), parse_rule(rule)
    urls = (request.app.state.tick_url, request.app.state.calendar_url)

    async def plan(conn: Conn) -> GetPlan:
        return await product_plan(conn, urls, entry, parsed, *span)

    return plan


def _bars_reader(granularity: TickBarGranularity) -> ReadWindow:
    size = Granularity(granularity.value)

    async def read(conn: Conn, window: TickWindow, on_batch: OnBatch) -> int:
        return await read_bars(conn, window, size, on_batch)

    return read


async def _ticks(
    request: Request,
    conn: Conn,
    plan_for: PlanFor,
    limit: int,
    instants: tuple[datetime | None, datetime | None],
    fmt: ResponseFormat,
) -> Response:
    bounds = instant_bounds(*instants)
    plan, rows = await _serve(
        request, conn, plan_for, read_ticks, (limit, NARROW_TICKS), bounds.ns()
    )
    model = FuturesTicksResponse.from_plan(plan, rows, (bounds.lower, bounds.upper))
    return timeseries_response(model, fmt)


async def _bars(
    request: Request,
    conn: Conn,
    plan_for: PlanFor,
    limit: int,
    granularity: TickBarGranularity,
    fmt: ResponseFormat,
) -> Response:
    reader = _bars_reader(granularity)
    plan, rows = await _serve(request, conn, plan_for, reader, (limit, NARROW_WINDOW))
    model = FuturesBarsResponse.from_plan(plan, granularity.value, rows)
    return timeseries_response(model, fmt)


@router.get(
    "/contracts/{symbol}/ticks", response_class=Response, responses=_TICKS_RESPONSES
)
async def contract_ticks(
    request: Request,
    symbol: str,
    conn: TickConn,
    max_rows: MaxRows,
    start: StartParam = None,
    end: EndParam = None,
    from_: FromParam = None,
    until: UntilParam = None,
    fmt: Format = "json",
) -> Response:
    """One contract's ticks over ``[start, end)`` sessions, clipped to
    ``[from, until)``, in key order. Refused past the row ceiling."""
    plan_for = _contract_plan(request, symbol, start, end)
    return await _ticks(request, conn, plan_for, max_rows, (from_, until), fmt)


@router.get(
    "/contracts/{symbol}/bars", response_class=Response, responses=_BARS_RESPONSES
)
async def contract_bars(
    request: Request,
    symbol: str,
    conn: TickConn,
    max_rows: MaxRows,
    granularity: GranularityParam,
    start: StartParam = None,
    end: EndParam = None,
    fmt: Format = "json",
) -> Response:
    """One contract's session-bounded bars over ``[start, end)`` sessions."""
    plan_for = _contract_plan(request, symbol, start, end)
    return await _bars(request, conn, plan_for, max_rows, granularity, fmt)


@router.get(
    "/products/{product}/ticks", response_class=Response, responses=_TICKS_RESPONSES
)
async def product_ticks(
    request: Request,
    product: str,
    conn: TickConn,
    max_rows: MaxRows,
    rule: RuleParam = None,
    start: StartParam = None,
    end: EndParam = None,
    from_: FromParam = None,
    until: UntilParam = None,
    fmt: Format = "json",
) -> Response:
    """A product's ticks under ``rule``: each session from the contract the
    rule names (``segments``); ``unresolved`` sessions have no rows."""
    plan_for = _product_plan(request, product, rule, (start, end))
    return await _ticks(request, conn, plan_for, max_rows, (from_, until), fmt)


@router.get(
    "/products/{product}/bars", response_class=Response, responses=_BARS_RESPONSES
)
async def product_bars(
    request: Request,
    product: str,
    conn: TickConn,
    max_rows: MaxRows,
    granularity: GranularityParam,
    rule: RuleParam = None,
    start: StartParam = None,
    end: EndParam = None,
    fmt: Format = "json",
) -> Response:
    """A product's bars under ``rule``, one contract per session, never merged."""
    plan_for = _product_plan(request, product, rule, (start, end))
    return await _bars(request, conn, plan_for, max_rows, granularity, fmt)
