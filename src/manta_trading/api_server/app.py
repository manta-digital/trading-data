"""FastAPI application factory for the Manta Trading Data Serving API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from manta_trading.api_server.routes.bars import router as bars_router
from manta_trading.api_server.routes.gaps import router as gaps_router
from manta_trading.api_server.routes.health import router as health_router
from manta_trading.api_server.routes.status import router as status_router
from manta_trading.api_server.routes.symbols import router as symbols_router
from manta_trading.config import Settings
from manta_trading.logging import get_logger
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB
from manta_trading.version import package_version

_logger = get_logger(__name__)


def _configure_connection(conn: psycopg.Connection[Any]) -> None:
    """Configure session parameters for a pooled connection.

    Mirrors ``TimescaleMinuteDataDB._configure_connection`` but trimmed
    to the settings needed by the API server (UTC tz, work_mem,
    statement_timeout). Autocommit is toggled so ``SET`` does not leave
    the connection in INTRANS state.
    """
    conn.autocommit = True
    conn.execute("SET timezone = 'UTC'")
    conn.execute("SET work_mem = '512MB'")
    conn.execute("SET statement_timeout = '300s'")
    conn.autocommit = False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open and close the shared ``ConnectionPool`` over the app lifetime.

    Reads ``Settings().timescale_db_url`` at startup. Raises
    ``RuntimeError`` if the URL is not set — no silent fallback.
    """
    settings = Settings()
    if not settings.timescale_db_url:
        raise RuntimeError(
            "MT_TIMESCALE_DB_URL is required for the API server"
        )

    loop = asyncio.get_running_loop()
    pool = await loop.run_in_executor(
        None,
        lambda: ConnectionPool(
            str(settings.timescale_db_url),
            min_size=2,
            max_size=8,
            max_lifetime=3600.0,
            configure=_configure_connection,
        ),
    )
    app.state.db_pool = pool
    _logger.info("API server connection pool opened")
    conninfo = str(settings.timescale_db_url)
    app.state.minute_db = TimescaleMinuteDataDB(conninfo)
    app.state.daily_db = TimescaleDailyDataDB(conninfo)
    _logger.info("Minute and daily DB instances initialized")
    try:
        yield
    finally:
        pool.close()
        _logger.info("API server connection pool closed")


def create_app() -> FastAPI:
    """Build a configured FastAPI application."""
    app = FastAPI(
        title="Manta Trading API",
        description="Data serving API for OHLCV bars, symbol metadata, and gap status.",
        version=package_version(),
        lifespan=lifespan,
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
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        if exc.status_code == 404:
            return JSONResponse(status_code=404, content={"error": str(exc.detail)})
        return await _default_http_handler(request, exc)  # type: ignore[return-value]

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
