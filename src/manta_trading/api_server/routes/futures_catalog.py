"""Futures catalog routes: products, contracts, schedule (slice 231, TD1, TD5).

Every route depends on ``tick_connection``, so not-configured, unreachable and
migrations-pending are a ``503`` before any roll call (TD2). ``products`` and
``schedule`` call 229's roll functions, which open their own bounded
connections; the pooled checkout held for the request is what caps how many
run at once. Read failures map through ``tick_errors``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from manta_trading.api_server.models.futures_catalog import (
    ContractRecord,
    FuturesContractsResponse,
    FuturesProductsResponse,
    FuturesScheduleResponse,
    ProductRecord,
)
from manta_trading.api_server.models.responses import (
    GATEWAY_TIMEOUT_RESPONSE,
    TICK_UNAVAILABLE_RESPONSE,
)
from manta_trading.api_server.routes.futures_params import (
    EndParam,
    RuleParam,
    StartParam,
    parse_rule,
    product_entry,
)
from manta_trading.api_server.tick_access import tick_connection, tick_errors
from manta_trading.data.tick.active_contract import (
    build_schedule,
    error_lines,
    latest_lines,
)
from manta_trading.data.tick.catalog_reads import contracts_for
from manta_trading.data.tick.get_plan import resolve_range
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.roll_rules import STATUS_ROLL_RULES, RollRule
from manta_trading.data.tick.roll_types import RollInputError, RollSchedule
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

router = APIRouter(prefix="/api/v1/futures", tags=["futures"])

_TICK_ERRORS = TICK_UNAVAILABLE_RESPONSE | GATEWAY_TIMEOUT_RESPONSE
TickConn = Annotated[Conn, Depends(tick_connection)]


@router.get("/products", response_model=FuturesProductsResponse, responses=_TICK_ERRORS)
async def list_products(request: Request, _conn: TickConn) -> FuturesProductsResponse:
    """Every configured product, its calendar, and the active contract under
    each status rule for the latest complete session held.

    A refusal or broken invariant is that product's line ``error`` (HTTP 200);
    only an outage of the tick or calendar database is a ``503`` (TD2).
    """
    app = request.app
    products = []
    for entry in TICK_UNIVERSE:
        with tick_errors(app):
            try:
                lines = await latest_lines(
                    app.state.tick_url, app.state.calendar_url, entry, STATUS_ROLL_RULES
                )
            except (TickPreflightError, RollInputError) as exc:
                # Reported, not raised (229 TD9): the product's lines carry the
                # refusal and the other products still answer.
                lines = error_lines(entry.product, STATUS_ROLL_RULES, exc)
        products.append(ProductRecord.from_lines(entry.product, lines))
    return FuturesProductsResponse(products=products)


@router.get(
    "/products/{product}/contracts",
    response_model=FuturesContractsResponse,
    responses=_TICK_ERRORS,
)
async def list_contracts(
    request: Request, product: str, conn: TickConn
) -> FuturesContractsResponse:
    """Every instrument defined for ``product``, spreads included, with the
    first and last session held (``held`` null when none), by expiration."""
    entry = product_entry(product)
    with tick_errors(request.app):
        rows = await contracts_for(conn, entry.product)
    return FuturesContractsResponse(
        product=entry.product, contracts=[ContractRecord.from_catalog(r) for r in rows]
    )


@router.get(
    "/products/{product}/schedule",
    response_model=FuturesScheduleResponse,
    responses=_TICK_ERRORS,
)
async def product_schedule(
    request: Request,
    product: str,
    conn: TickConn,
    rule: RuleParam = None,
    start: StartParam = None,
    end: EndParam = None,
) -> FuturesScheduleResponse:
    """The contract serving each session in ``[start, end)`` under ``rule``,
    as runs (``segments``), per session (``answers``) and the sessions the rule
    could not answer (``unresolved``)."""
    entry = product_entry(product)
    parsed = parse_rule(rule)
    with tick_errors(request.app):
        schedule = await _schedule(request, conn, entry, parsed, (start, end))
    return FuturesScheduleResponse.from_schedule(schedule)


async def _schedule(
    request: Request,
    conn: Conn,
    entry: TickUniverseEntry,
    rule: RollRule,
    span: tuple[date | None, date | None],
) -> RollSchedule:
    """The schedule over ``span`` (defaulted to the held sessions); empty when
    nothing is held and a side was omitted, as ``get``'s empty plan is."""
    resolved = await resolve_range(conn, entry, *span)
    if resolved is None:
        return RollSchedule(entry.product, rule, ())
    state = request.app.state
    return await build_schedule(
        state.tick_url, state.calendar_url, entry, rule, *resolved
    )
