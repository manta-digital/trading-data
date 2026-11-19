"""Futures tick routes, by contract or by product, and the plan builders the
futures series routes share (slice 231, TD1, TD3–TD6).

Both tick routes go through :func:`_serve`: one snapshot read (``read_series``)
under the row ceiling, the one error mapping (``tick_errors``), then the model
and ``send_timeseries`` (JSON or msgpack). ``start``/``end`` are session dates
with ``end`` exclusive; ticks also take half-open ``[from, until)`` instants.
Time bars live in ``futures_time_bars`` (309), tick bars in
``futures_tick_bars`` (308).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date, datetime
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from manta_trading.api_server.admission import (
    NARROW_TICKS,
    over_ceiling,
)
from manta_trading.api_server.deps import get_max_bars
from manta_trading.api_server.futures_read import (
    PlanFor,
    ReadWindow,
    RowCeilingExceeded,
    read_series,
)
from manta_trading.api_server.models.futures_ticks import FuturesTicksResponse
from manta_trading.api_server.models.responses import (
    GATEWAY_TIMEOUT_RESPONSE,
    NOT_FOUND_RESPONSE,
    TICK_UNAVAILABLE_RESPONSE,
)
from manta_trading.api_server.routes.futures_params import (
    EndParam,
    FromParam,
    RuleParam,
    StartParam,
    UntilParam,
    instant_bounds,
    parse_rule,
    product_entry,
)
from manta_trading.api_server.serialization import (
    ResponseFormat,
    send_timeseries,
    timeseries_200,
)
from manta_trading.api_server.tick_access import tick_connection, tick_errors
from manta_trading.data.tick.get_plan import GetPlan, contract_plan, product_plan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_reads import Row, read_ticks

router = APIRouter(prefix="/api/v1/futures", tags=["futures"])

TickConn = Annotated[Conn, Depends(tick_connection)]
MaxRows = Annotated[int, Depends(get_max_bars)]
Format = Annotated[ResponseFormat, Query(alias="format")]
_TICKS_RESPONSES = (
    timeseries_200(FuturesTicksResponse)
    | TICK_UNAVAILABLE_RESPONSE
    | GATEWAY_TIMEOUT_RESPONSE
    | NOT_FOUND_RESPONSE
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


def contract_plan_for(
    request: Request,
    symbol: str,
    span: tuple[date | None, date | None],
    family: SchemaFamily,
    range_over: Sequence[SchemaFamily] | None = None,
    instants: tuple[datetime | None, datetime | None] = (None, None),
) -> PlanFor:
    """The contract plan of ``family``, built on the read's own connection;
    a missing side of ``span`` comes from the sessions ``instants`` reach, else
    defaults over ``range_over`` (``get_plan``)."""
    calendar_url = request.app.state.calendar_url
    bounds = instant_bounds(*instants)

    async def plan(conn: Conn) -> GetPlan:
        return await contract_plan(
            conn,
            calendar_url,
            symbol,
            *span,
            family,
            range_over,
            bounds=(bounds.lower, bounds.upper),
        )

    return plan


def product_plan_for(
    request: Request,
    product: str,
    rule: str | None,
    span: tuple[date | None, date | None],
    family: SchemaFamily,
    range_over: Sequence[SchemaFamily] | None = None,
    instants: tuple[datetime | None, datetime | None] = (None, None),
) -> PlanFor:
    """Resolves the product (404), the rule and ``instants`` (422) before any
    read; ``instants`` narrow a missing side of ``span`` as for contracts."""
    entry, parsed = product_entry(product), parse_rule(rule)
    urls = (request.app.state.tick_url, request.app.state.calendar_url)
    bounds = instant_bounds(*instants)

    async def plan(conn: Conn) -> GetPlan:
        return await product_plan(
            conn,
            urls,
            entry,
            parsed,
            *span,
            family,
            range_over,
            bounds=(bounds.lower, bounds.upper),
        )

    return plan


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
    build = partial(
        FuturesTicksResponse.from_plan, plan, rows, (bounds.lower, bounds.upper)
    )
    # The pivot touches every tick: off the loop, as the encode is (308 D6).
    model = await asyncio.get_running_loop().run_in_executor(None, build)
    return await send_timeseries(request, model, fmt)


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
    plan_for = contract_plan_for(
        request, symbol, (start, end), SchemaFamily.TICK_TIER, instants=(from_, until)
    )
    return await _ticks(request, conn, plan_for, max_rows, (from_, until), fmt)


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
    plan_for = product_plan_for(
        request,
        product,
        rule,
        (start, end),
        SchemaFamily.TICK_TIER,
        instants=(from_, until),
    )
    return await _ticks(request, conn, plan_for, max_rows, (from_, until), fmt)
