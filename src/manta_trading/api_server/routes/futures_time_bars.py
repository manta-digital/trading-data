"""Futures time bars, by contract or by product (slice 309; 231 TD1, TD3-TD6).

Each session's bars come from held provider bars that can make the size, else
are cut from its ticks (``time_bar_source``). One :class:`TimeBarRead` per
request admits the tick-cut sessions on their trades before any tick is read,
reads each window from its source, and applies ``[from, until)`` by bar start,
so ``read_series`` gets no bounds and every window arrives unclipped. The
response names the source of each run of sessions (``sources``) and every
parameter used (``applied``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette import status as http_status

from manta_trading.api_server.admission import (
    CLIENT_CLOSED_REQUEST,
    NARROW_TIME_BARS,
    over_ceiling,
)
from manta_trading.api_server.deps import get_max_time_bar_trades
from manta_trading.api_server.futures_read import (
    PlanFor,
    RowCeilingExceeded,
    read_series,
)
from manta_trading.api_server.models.futures import (
    AppliedBarParams,
    FuturesBarsResponse,
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
from manta_trading.data.tick.get_plan import TIME_BAR_RANGE, GetPlan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_bar_reads import ReadAbandoned
from manta_trading.data.tick.tick_reads import Row
from manta_trading.data.tick.time_bar_reads import (
    LedgerCountExceeded,
    TimeBarRead,
    TimeBarScanExceeded,
)
from manta_trading.data.tick.time_bar_size import (
    BAR_SIZE_FORM,
    BarSize,
    parse_bar_size,
)
from manta_trading.data.tick.time_bar_source import served_plan, source_runs

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/futures", tags=["futures"])

MaxTrades = Annotated[int, Depends(get_max_time_bar_trades)]
GranularityParam = Annotated[
    str,
    Query(
        description=(
            f"Bar size: {BAR_SIZE_FORM}. 1d is the whole CME session. Bars"
            " start at the session open and never cross a session."
        )
    ),
]
_RESPONSES = (
    timeseries_200(FuturesBarsResponse)
    | TICK_UNAVAILABLE_RESPONSE
    | GATEWAY_TIMEOUT_RESPONSE
)


def bar_size(text: str) -> BarSize:
    """``text`` parsed, or ``422`` naming the accepted form."""
    try:
        return parse_bar_size(text)
    except ValueError as exc:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from exc


def _ledger_mismatch(exc: LedgerCountExceeded) -> HTTPException:
    """Log the session and both counts; the stable ``500``."""
    _logger.error(
        "time bars: session %s of %s (instrument %s) read %s trades, over its"
        " ledger count %s",
        exc.session_date,
        exc.raw_symbol,
        exc.instrument_id,
        exc.read,
        exc.ledger,
    )
    return HTTPException(
        http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        f"stored ticks for session {exc.session_date} of {exc.raw_symbol} disagree"
        " with the ingest ledger; see the server log",
    )


async def _read(
    request: Request, conn: Conn, plan_for: PlanFor, reader: TimeBarRead, max_rows: int
) -> tuple[GetPlan, list[Row]]:
    """Admit, read and collect; every failure but the disconnect mapped once."""
    try:
        with tick_errors(request.app):
            return await read_series(
                conn, plan_for, reader.read, max_rows, admit=reader.admit
            )
    except RowCeilingExceeded:
        raise over_ceiling(max_rows, remedy=NARROW_TIME_BARS) from None
    except TimeBarScanExceeded as exc:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from None
    except LedgerCountExceeded as exc:
        raise _ledger_mismatch(exc) from exc


async def _time_bars(
    request: Request,
    conn: Conn,
    plan_for: PlanFor,
    query: tuple[BarSize, datetime | None, datetime | None],
    limits: tuple[int, int],
    fmt: ResponseFormat,
) -> Response:
    """``query`` is ``(size, from, until)``; ``limits`` (rows, trades)."""
    size, from_, until = query
    max_rows, max_trades = limits
    bounds = instant_bounds(from_, until)
    reader = TimeBarRead(
        size,
        request.app.state.calendar_url,
        max_trades=max_trades,
        bounds=bounds.ns(),
        stop=request.is_disconnected,
    )
    try:
        plan, rows = await _read(request, conn, plan_for, reader, max_rows)
    except ReadAbandoned:
        # The client disconnected mid-read: nobody is left to answer, so this is
        # not an error to log (308 D10); the snapshot already rolled back.
        return Response(status_code=CLIENT_CLOSED_REQUEST)
    runs = source_runs(plan, reader.sources, reader.returned)
    applied = AppliedBarParams.from_plan(
        plan, size.token, (bounds.lower, bounds.upper), fmt
    )
    build = partial(
        FuturesBarsResponse.from_plan,
        served_plan(plan, reader.sources),
        runs,
        applied,
        rows,
    )
    # Validating tens of thousands of bars blocks the loop: off it, as the
    # ticks and tick-bar routes do.
    model = await asyncio.get_running_loop().run_in_executor(None, build)
    return await send_timeseries(request, model, fmt)


@router.get("/contracts/{symbol}/bars", response_class=Response, responses=_RESPONSES)
async def contract_bars(
    request: Request,
    symbol: str,
    conn: TickConn,
    max_rows: MaxRows,
    max_trades: MaxTrades,
    granularity: GranularityParam,
    start: StartParam = None,
    end: EndParam = None,
    from_: FromParam = None,
    until: UntilParam = None,
    fmt: Format = "json",
) -> Response:
    """One contract's time bars from held bars or ticks over ``[start, end)``
    sessions, those starting in ``[from, until)``. Refused past the trade limit
    or the row ceiling."""
    size = bar_size(granularity)
    plan_for = contract_plan_for(
        request, symbol, (start, end), SchemaFamily.TICK_TIER, TIME_BAR_RANGE
    )
    query = (size, from_, until)
    return await _time_bars(request, conn, plan_for, query, (max_rows, max_trades), fmt)


@router.get("/products/{product}/bars", response_class=Response, responses=_RESPONSES)
async def product_bars(
    request: Request,
    product: str,
    conn: TickConn,
    max_rows: MaxRows,
    max_trades: MaxTrades,
    granularity: GranularityParam,
    rule: RuleParam = None,
    start: StartParam = None,
    end: EndParam = None,
    from_: FromParam = None,
    until: UntilParam = None,
    fmt: Format = "json",
) -> Response:
    """A product's time bars from held bars or ticks under ``rule``, one
    contract per session, never merged."""
    size = bar_size(granularity)
    plan_for = product_plan_for(
        request, product, rule, (start, end), SchemaFamily.TICK_TIER, TIME_BAR_RANGE
    )
    query = (size, from_, until)
    return await _time_bars(request, conn, plan_for, query, (max_rows, max_trades), fmt)
