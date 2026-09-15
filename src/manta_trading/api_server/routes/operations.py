"""Route handlers for ``GET /api/v1/overview`` and ``GET /api/v1/credits``.

Thin routes over slice 922's reader (189 D3): ``gather_db_facts`` reads,
``build_overview`` derives, and these handlers translate. Nothing here
computes running/last-run/next-firing a second time — a derivation appearing
in this module is SC2 failing, not a local optimization.

The two routes are siblings rather than one endpoint (189 D2) because their
failure modes are unrelated. ``/api/v1/overview`` is pure database, so it is
safe to poll; ``/api/v1/credits`` reaches a third party over HTTPS, where a
failure means one missing line rather than nothing being true.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request

from manta_trading.api.eodhd_account import fetch_credit_usage
from manta_trading.api.eodhd_sync import redact_token
from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.operations import (
    CreditsRecord,
    CreditsResponse,
    OverviewResponse,
)
from manta_trading.api_server.models.responses import GATEWAY_TIMEOUT_RESPONSE
from manta_trading.cli.commands.overview import build_overview, gather_db_facts
from manta_trading.cli.overview_types import CREDITS_NO_KEY, credits_unavailable
from manta_trading.logging import get_logger

router = APIRouter()

_logger = get_logger(__name__)


@router.get("/api/v1/overview", responses=GATEWAY_TIMEOUT_RESPONSE)
async def get_overview(
    request: Request,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)],
) -> OverviewResponse:
    """Return pass state, source freshness, health and universe accounting.

    The same facts as ``mt data overview``, with two deliberate differences
    recorded on the response models: the credit line lives at
    ``/api/v1/credits`` (D2) and ``abandoned`` is omitted (D4).

    No query or path parameters, so there is no 404 and no 422 (D6). A
    database predating migration 055 is a 200 with every pass empty, not a
    500 — 922's reader already degrades that way and this route inherits it.
    """
    settings = request.app.state.settings
    loop = asyncio.get_running_loop()

    def _read() -> OverviewResponse:
        facts = gather_db_facts(db, settings)
        # D4: pid_alive is forced True so no row is ever judged abandoned.
        # 922 tests the recorded pid against the *local* process table, which
        # is sound for a CLI running beside the pass and wrong here: a pid
        # recorded on another host says nothing about a process on this one.
        # A bare `pid_is_alive` in this call would be a bug, not a
        # simplification. The models drop the field rather than publish a
        # value that would be structurally false for every row.
        overview = build_overview(facts, pid_alive=lambda _pid: True)
        return OverviewResponse.from_overview(overview)

    return await loop.run_in_executor(None, _read)


@router.get("/api/v1/credits")
async def get_credits(request: Request) -> CreditsResponse:
    """Return the day's EODHD credit position.

    Always 200 (D6/SC6): an unset key is a setting and an unreachable
    provider is a reported condition, so both arrive as ``error`` text with
    ``credits: null`` rather than as an HTTP failure.

    **No** ``GATEWAY_TIMEOUT_RESPONSE`` here, deliberately (D8). This route
    issues no statement and takes no parameters, so a statement timeout is
    not among its outcomes and the shared constant's remedy — "narrow the
    requested range or use a coarser granularity" — would be false advice in
    the published schema. Do not add it for symmetry with the sibling route.

    No ``Depends(get_db)`` either: the route needs no connection, and the pool
    is small enough (``max_size=8``) that holding one across a third party's
    response time would be a real cap on concurrency.
    """
    settings = request.app.state.settings
    api_key = settings.eodhd_api_key
    if not api_key:
        return CreditsResponse(credits=None, error=CREDITS_NO_KEY)

    loop = asyncio.get_running_loop()
    try:
        usage = await loop.run_in_executor(
            None, lambda: fetch_credit_usage(str(api_key))
        )
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        # Swallowed on purpose, and narrowly scoped to one outbound call: a
        # provider outage is a condition this endpoint reports, not a fault of
        # this server, and the 200-with-error shape is the route's contract
        # (D6). Re-raising would turn a third party's bad afternoon into a 500
        # on our API.
        #
        # Redacted before it reaches either the body or the journal: the
        # message can carry the request URL, and the API key rides in that
        # URL's query string (922 review F001).
        reason = redact_token(str(exc))
        # exc_info so the journal keeps the stack. The catch is broad by
        # contract, and a one-line WARNING would hide a programming error
        # inside the fetch behind a plausible "unavailable" line (922
        # re-review F003). The logged message itself stays redacted.
        _logger.warning(
            "credits: lookup failed: %s", reason, exc_info=True
        )
        return CreditsResponse(credits=None, error=credits_unavailable(reason))

    return CreditsResponse(credits=CreditsRecord.from_usage(usage), error=None)
