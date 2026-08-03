"""Route handler for ``GET /api/v1/status`` (slice 185).

A thin translation layer over slice 167's guarded ``data_status`` accessors: it
calls ``status_queries`` and maps the returned dataclasses onto Pydantic
response models. No SQL and no freshness logic live here — both already exist
and are already proven by ``mt data status``.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status

from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import (
    CoverageStatus,
    StatusResponse,
    StatusRowRecord,
)
from manta_trading.cli.rendering.status_table import HealthStatus
from manta_trading.data.maintenance.status_queries import (
    fetch_all_health_counts_with_freshness,
    fetch_status_rows_with_freshness,
)

router = APIRouter()

_DEFAULT_HEALTH_FILTER: tuple[str, ...] = tuple(
    member.value for member in HealthStatus if member is not HealthStatus.OK
)
"""The CLI's default ``--health`` set: every health value except ``OK``.

Derived from ``HealthStatus`` rather than restated, so adding a health value
extends the API default and the CLI default together.
"""


def _resolve_health_filter(health: str | None, *, all_rows: bool) -> list[str] | None:
    """Resolve the ``health_filter`` argument, mirroring ``mt data status``.

    ``all=true`` overrides ``health`` with "no filter" (the CLI's ``--all``);
    an omitted ``health`` falls back to the CLI's non-``OK`` default.

    Raises:
        HTTPException: 422 when a token is not a ``HealthStatus`` value. 422 is
            the same status FastAPI already returns for an invalid
            ``granularity`` on the bars route, so one malformed-query contract
            covers both.
    """
    if all_rows:
        return None
    if health is None:
        return list(_DEFAULT_HEALTH_FILTER)

    tokens = [token.strip().upper() for token in health.split(",") if token.strip()]
    valid = {member.value for member in HealthStatus}
    invalid = [token for token in tokens if token not in valid]
    if invalid:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Invalid health values: {', '.join(invalid)}. "
                f"Valid: {', '.join(sorted(valid))}"
            ),
        )
    return tokens


@router.get("/api/v1/status")
async def get_status(
    db: Annotated[psycopg.Connection[Any], Depends(get_db)],
    symbol: str | None = None,
    health: str | None = None,
    granularity: Literal["daily", "minute"] | None = None,
    all_rows: Annotated[bool, Query(alias="all")] = False,
) -> StatusResponse:
    """Return ``data_status`` health rows plus coverage-cagg freshness.

    An unknown or non-matching ``symbol`` is a 200 with ``rows: []`` (D5) —
    ``scope`` still reports ``"symbol"`` so a client can tell "filtered scope,
    nothing matched" from "whole registry".

    DB failures are not caught here (D9): the freshness probes never raise (a
    failed probe is a stale verdict), and a genuine ``data_status`` query
    failure propagates to the global 500 handler in ``app.py``.
    """
    health_filter = _resolve_health_filter(health, all_rows=all_rows)
    loop = asyncio.get_running_loop()

    def _fetch() -> tuple[list[StatusRowRecord], dict[str, int], CoverageStatus]:
        # Sequential on purpose, not gathered: both calls share one pooled
        # connection, and psycopg serializes execution on a connection's lock,
        # so concurrent dispatch would buy no parallelism while letting the
        # freshness guard's statement_timeout save/restore interleave between
        # two threads. Running in order also lets the health-count fetch hit
        # the verdict cache the row fetch just warmed — the same amortization
        # `mt data status` relies on.
        rows, coverage = fetch_status_rows_with_freshness(
            db,
            symbol=symbol,
            health_filter=health_filter,
            granularity=granularity,
        )
        summary, _ = fetch_all_health_counts_with_freshness(db)
        return (
            [StatusRowRecord.from_status_row(row) for row in rows],
            summary,
            CoverageStatus.from_freshness(coverage),
        )

    records, summary, coverage_status = await loop.run_in_executor(None, _fetch)

    return StatusResponse(
        scope="symbol" if symbol is not None else "all",
        symbol=symbol,
        count=len(records),
        rows=records,
        summary=summary,
        coverage=coverage_status,
    )
