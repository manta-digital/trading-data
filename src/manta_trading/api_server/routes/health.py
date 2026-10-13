"""Health endpoint: ``GET /api/v1/health``."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette import status as http_status

from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import HealthResponse
from manta_trading.api_server.tick_access import probe_tick
from manta_trading.data.maintenance.status_coverage import check_coverage_freshness
from manta_trading.data.tick.line_state import TickLineState
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
async def health(
    request: Request,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)],
) -> JSONResponse:
    """Liveness/readiness probe.

    Returns HTTP 200 in every case — callers distinguish database state via
    the ``db`` and ``tick_db`` fields of the body, not the status code.

    ``coverage`` is a coarse pipeline-health signal: exactly the two coverage
    caggs behind ``data_status`` (slice 185 D6), not a probe of all seven
    bar-serving caggs. Per-granularity truth belongs to the bars endpoint's own
    ``is_stale`` field.

    ``tick_db`` (231 TD7) is the tick pool's state; a tick outage never changes
    the primary fields. The blocking primary probe runs in the executor and the
    tick probe is awaited.
    """
    loop = asyncio.get_running_loop()
    primary = await loop.run_in_executor(None, _probe_primary, db)
    tick = await probe_tick(request.app)
    body = HealthResponse(
        **primary,
        tick_db=TickLineState.OK if tick is None else tick.state,
        tick_detail=None if tick is None else tick.detail,
    )
    return JSONResponse(
        status_code=http_status.HTTP_200_OK, content=body.model_dump(exclude_none=True)
    )


def _probe_primary(db: psycopg.Connection[Any]) -> dict[str, Any]:
    """Blocking: ``SELECT 1``, then coverage freshness when the database
    answered; the primary fields of the body."""
    try:
        db.execute("SELECT 1")
    except psycopg.Error as exc:
        _logger.warning("Health check DB query failed: %s", exc)
        return {"status": "ok", "db": "error", "detail": str(exc)}
    freshness = check_coverage_freshness(db)
    return {
        "status": "ok",
        "db": "ok",
        "coverage": "stale" if freshness.is_stale else "ok",
    }
