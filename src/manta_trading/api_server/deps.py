"""FastAPI dependencies for the Data Serving API."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import psycopg
from fastapi import Request

from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB


def get_db(request: Request) -> Generator[psycopg.Connection[Any], None, None]:
    """Yield a pooled psycopg connection for the request.

    The connection pool is owned by ``app.state.db_pool`` and is opened
    by the lifespan hook in :mod:`manta_trading.api_server.app`.
    """
    pool = request.app.state.db_pool
    with pool.connection() as conn:
        yield conn


def get_minute_db(request: Request) -> TimescaleMinuteDataDB:
    """Return the shared ``TimescaleMinuteDataDB`` instance from app state."""
    return request.app.state.minute_db  # type: ignore[no-any-return]


def get_daily_db(request: Request) -> TimescaleDailyDataDB:
    """Return the shared ``TimescaleDailyDataDB`` instance from app state."""
    return request.app.state.daily_db  # type: ignore[no-any-return]
