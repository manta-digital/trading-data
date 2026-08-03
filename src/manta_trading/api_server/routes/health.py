"""Health endpoint: ``GET /api/v1/health``."""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import HealthResponse
from manta_trading.data.maintenance.status_coverage import check_coverage_freshness
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
def health(
    db: Annotated[psycopg.Connection[Any], Depends(get_db)],
) -> JSONResponse:
    """Liveness/readiness probe.

    Returns HTTP 200 in both healthy and DB-error cases — callers
    distinguish DB state via the ``db`` field of the response body.

    ``coverage`` is a coarse pipeline-health signal: exactly the two coverage
    caggs behind ``data_status`` (slice 185 D6), not a probe of all seven
    bar-serving caggs. Per-granularity truth belongs to the bars endpoint's own
    ``is_stale`` field.

    Deliberately a **sync** handler: FastAPI runs sync routes in a worker
    thread, so the blocking freshness probe needs no ``run_in_executor``.
    """
    try:
        db.execute("SELECT 1")
    except psycopg.Error as exc:
        _logger.warning("Health check DB query failed: %s", exc)
        body = HealthResponse(status="ok", db="error", detail=str(exc))
        return JSONResponse(status_code=200, content=body.model_dump(exclude_none=True))

    freshness = check_coverage_freshness(db)
    body = HealthResponse(
        status="ok",
        db="ok",
        coverage="stale" if freshness.is_stale else "ok",
    )
    return JSONResponse(status_code=200, content=body.model_dump(exclude_none=True))
