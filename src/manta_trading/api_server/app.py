"""FastAPI application factory for the Manta Trading Data Serving API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from manta_trading.api_server.routes.bars import router as bars_router
from manta_trading.api_server.routes.gaps import router as gaps_router
from manta_trading.api_server.routes.health import router as health_router
from manta_trading.api_server.routes.status import router as status_router
from manta_trading.api_server.routes.symbols import router as symbols_router
from manta_trading.config import Settings
from manta_trading.constants import API_SERVING_SESSION, DbSessionSettings
from manta_trading.logging import get_logger
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB
from manta_trading.version import package_version

_logger = get_logger(__name__)


def make_configure_connection(
    session: DbSessionSettings,
) -> Callable[[psycopg.Connection[Any]], None]:
    """Build the pool ``configure`` hook for a given session budget.

    Mirrors ``TimescaleMinuteDataDB._configure_connection`` but trimmed to the
    settings needed by the API server (UTC tz, work_mem, statement_timeout).
    Autocommit is toggled so ``SET`` does not leave the connection in INTRANS
    state.

    A factory rather than a module function because ``statement_timeout`` is
    operator-settable (186 D9) and must be resolved from ``Settings`` at
    startup — the same value the two DB pools are constructed with, so all
    three pools the API owns agree.
    """

    def configure(conn: psycopg.Connection[Any]) -> None:
        conn.autocommit = True
        conn.execute("SET timezone = 'UTC'")
        conn.execute(f"SET work_mem = '{session.work_mem}'")
        conn.execute(f"SET statement_timeout = '{session.statement_timeout}'")
        conn.autocommit = False

    return configure


@asynccontextmanager
async def lifespan(
    app: FastAPI, db_url: str | None = None
) -> AsyncIterator[None]:
    """Open and close the shared ``ConnectionPool`` over the app lifetime.

    Reads ``Settings().timescale_db_url`` at startup. Raises
    ``RuntimeError`` if the URL is not set — no silent fallback.

    ``db_url`` overrides that lookup when supplied (187 D9). It exists for the
    load tier, which must point the API at an ephemeral database and cannot do
    so through the environment: ``test_load_tier_never_references_prod_db_url``
    fails any load-test line that reads ``MT_TIMESCALE_DB_URL``. An explicit
    parameter is the seam; an environment side channel would defeat that rule
    rather than satisfy it. ``None`` means "read ``Settings()``", which is every
    production path, unchanged.
    """
    settings = Settings()
    resolved_url = db_url or settings.timescale_db_url
    if not resolved_url:
        raise RuntimeError(
            "MT_TIMESCALE_DB_URL is required for the API server"
        )

    # D9: both policy ceilings are resolved once, here, and held on app.state.
    # Changing an override requires a restart — the same contract as
    # MT_TIMESCALE_DB_URL — so no request pays for re-reading the environment.
    app.state.max_bars_per_request = settings.api_max_bars_per_request
    app.state.statement_timeout = settings.api_statement_timeout
    session = DbSessionSettings(
        work_mem=API_SERVING_SESSION.work_mem,
        statement_timeout=settings.api_statement_timeout,
    )

    loop = asyncio.get_running_loop()
    pool = await loop.run_in_executor(
        None,
        lambda: ConnectionPool(
            str(resolved_url),
            min_size=2,
            max_size=8,
            max_lifetime=3600.0,
            configure=make_configure_connection(session),
        ),
    )
    app.state.db_pool = pool
    _logger.info("API server connection pool opened")
    conninfo = str(resolved_url)
    # All three pools get the same session budget (186 D1): the bars path runs
    # on the two class-owned pools, not on app.state.db_pool.
    app.state.minute_db = TimescaleMinuteDataDB(conninfo, session=session)
    app.state.daily_db = TimescaleDailyDataDB(conninfo, session=session)
    _logger.info(
        "Minute and daily DB instances initialized "
        "(work_mem=%s, statement_timeout=%s, max_bars=%d)",
        session.work_mem,
        session.statement_timeout,
        app.state.max_bars_per_request,
    )
    try:
        yield
    finally:
        pool.close()
        _logger.info("API server connection pool closed")


def create_app(db_url: str | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    Args:
        db_url: Database URL for the connection pool. ``None`` — every
            production path — reads ``Settings().timescale_db_url`` exactly as
            before. The load tier passes an ephemeral database explicitly
            (187 D9); see ``lifespan`` for why this is a parameter and not an
            environment variable.
    """
    app = FastAPI(
        title="Manta Trading API",
        description="Data serving API for OHLCV bars, symbol metadata, and gap status.",
        version=package_version(),
        # Bound rather than passed through app.state: the pool must be opened
        # before the first request, and app.state is not populated until
        # lifespan itself runs.
        lifespan=partial(lifespan, db_url=db_url),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(bars_router)
    app.include_router(symbols_router)
    app.include_router(gaps_router)
    app.include_router(status_router)

    @app.exception_handler(HTTPException)
    async def _custom_http_exception_handler(
        _request: Request, exc: HTTPException
    ) -> JSONResponse:
        """Emit one error shape for every error this codebase raises (186 D6).

        Widened from 404-only: the status route's 422s and the bars route's
        range rejections inherit it without constructing bodies of their own.
        FastAPI's ``RequestValidationError`` is handled separately and keeps its
        native ``detail`` list — it carries per-field ``loc``/``msg`` a
        flattened string would lose. That exception is documented in the README
        and asserted in the test suite so it cannot be unified by accident.
        """
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": str(exc.detail)},
            headers=exc.headers,
        )

    @app.exception_handler(psycopg.errors.QueryCanceled)
    async def _query_canceled_handler(
        request: Request, exc: psycopg.errors.QueryCanceled
    ) -> JSONResponse:
        """Map a statement-timeout cancellation to ``504`` (186 D10).

        A process-boundary handler by design: every route issues a DB query and
        every one can be cancelled, so a per-route ``try/except`` would be the
        same clause four times and would omit whichever route is added next.
        It is strictly narrower than the ``Exception`` handler below and takes
        precedence, so every non-cancellation fault still returns a sanitized
        ``500``.

        Freshness probes cannot reach here — ``cagg_freshness`` catches
        ``psycopg.Error`` internally and converts a timeout into a stale
        verdict (168 D3, 185 D9) — so a ``504`` always means a *data* query was
        cancelled, which is what makes "narrow the range" actionable advice.

        Logged at WARNING, not ERROR: this is a handled, operator-actionable
        signal that the bar ceiling and the timeout are out of step, and it
        must be visible without masquerading as a crash.
        """
        budget = request.app.state.statement_timeout
        _logger.warning(
            "Query cancelled at the %s statement_timeout on %s %s%s: %s",
            budget,
            request.method,
            request.url.path,
            f"?{request.url.query}" if request.url.query else "",
            exc,
        )
        return JSONResponse(
            status_code=504,
            content={
                "error": (
                    f"query exceeded the server's {budget} budget; narrow the "
                    "requested range or use a coarser granularity"
                )
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        _logger.exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500, content={"error": "internal server error"}
        )

    return app
