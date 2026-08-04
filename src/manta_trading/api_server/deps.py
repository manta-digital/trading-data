"""FastAPI dependencies for the Data Serving API."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import psycopg
from fastapi import Request
from psycopg_pool import ConnectionPool

from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB


def get_db(request: Request) -> Generator[psycopg.Connection[Any], None, None]:
    """Yield a pooled psycopg connection for the request.

    The connection pool is owned by ``app.state.db_pool`` and is opened
    by the lifespan hook in :mod:`manta_trading.api_server.app`.

    The connection is held for the **whole request**. Routes that need one only
    for part of their work should depend on :func:`get_db_pool` instead and
    scope the checkout themselves — the pool is small (``max_size=8``), so an
    unused-but-held connection is a real cap on concurrency.
    """
    pool = request.app.state.db_pool
    with pool.connection() as conn:
        yield conn


def get_db_pool(request: Request) -> ConnectionPool[psycopg.Connection[Any]]:
    """Return the shared connection pool without checking out a connection.

    For routes that need a connection for only a fraction of their runtime.
    ``bars.py`` is the case that motivated this: it needs a connection solely
    for the freshness probe, and for raw-table granularities it needs none at
    all — holding one for the full request would let a handful of concurrent
    bars requests exhaust the pool and stall every other endpoint.
    """
    return request.app.state.db_pool  # type: ignore[no-any-return]


def get_max_bars(request: Request) -> int:
    """Return the configured ceiling on estimated bars per request (186 D9).

    Resolved from ``MT_API_MAX_BARS_PER_REQUEST`` once in the lifespan hook and
    held on ``app.state``; this reads that value rather than re-instantiating
    ``Settings`` per request. Changing the override requires a restart.
    """
    return request.app.state.max_bars_per_request  # type: ignore[no-any-return]


def get_minute_db(request: Request) -> TimescaleMinuteDataDB:
    """Return the shared ``TimescaleMinuteDataDB`` instance from app state."""
    return request.app.state.minute_db  # type: ignore[no-any-return]


def get_daily_db(request: Request) -> TimescaleDailyDataDB:
    """Return the shared ``TimescaleDailyDataDB`` instance from app state."""
    return request.app.state.daily_db  # type: ignore[no-any-return]
