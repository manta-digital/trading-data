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
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request

from manta_trading.api.credit_report import read_credits
from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.operations import (
    CreditsRecord,
    CreditsResponse,
    OverviewResponse,
)
from manta_trading.api_server.models.responses import GATEWAY_TIMEOUT_RESPONSE
from manta_trading.cli.commands.overview import build_overview, gather_db_facts
from manta_trading.logging import get_logger

router = APIRouter()

_logger = get_logger(__name__)

CREDIT_EXECUTOR_THREADS = 2
"""Threads reserved for the outbound credit call (189 D8).

Small on purpose. The route reaches one endpoint whose own timeout is
``EODHD_ACCOUNT_TIMEOUT_SECONDS``, so two threads are enough to serve
concurrent callers without queueing behind each other for long, and the point
is the *ceiling*, not the throughput: however many credit requests arrive, they
can occupy only these threads.
"""


def make_credit_executor() -> ThreadPoolExecutor:
    """A dedicated pool so a slow provider cannot reach the database routes.

    **Measured, not precautionary.** With the fetch stubbed to block for the
    full account timeout and 36 concurrent ``/api/v1/credits`` requests against
    the default pool of 32 threads, the first ``/api/v1/overview`` request took
    **4.564 s** — it queued behind a blocked thread and was released only when
    a credit call timed out. Every route dispatches through
    ``run_in_executor(None, …)``, which shares one default pool of
    ``min(32, cpu + 4)``, so the coupling D8 predicted is real and
    reproducible.

    Widening the shared pool was the wrong fix and is deliberately not what
    this does: it would move the cliff to a higher concurrency rather than
    remove it, because *any* bound on the shared pool is one a stuck provider
    can exhaust. Confining the credit call to its own threads means a database
    route never waits on one, at any concurrency.

    Built here and owned by ``lifespan`` rather than created at import
    (189 code review F001), so its lifetime matches ``db_pool``'s and every
    other shared resource: one per app, so a test app and the production app
    never share threads, and the shutdown path can see it.
    """
    return ThreadPoolExecutor(
        max_workers=CREDIT_EXECUTOR_THREADS, thread_name_prefix="credits"
    )


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

    The fetch runs on the app's own credit executor rather than the default
    pool,
    so a provider that will not answer cannot hold threads ``/api/v1/overview``
    and ``/api/v1/bars`` need (D8). That is measured behavior, not caution —
    see the executor's own docstring for the figure.
    """
    settings = request.app.state.settings
    loop = asyncio.get_running_loop()
    # One policy for "report the credit position, never raise", shared with
    # `mt data overview` (189 code review F003): the unset-key case, the broad
    # catch scoped to the outbound call, the redaction that keeps the key out
    # of the body and the journal, and the exc_info log all live in
    # `read_credits`. Two copies of that is one place for the redaction to be
    # forgotten.
    #
    # Dispatched whole rather than just the fetch, so the *blocking* part —
    # which is the HTTPS call inside it — is what lands on the dedicated
    # executor and never on a thread the DB routes need (D8). See
    # make_credit_executor.
    report = await loop.run_in_executor(
        request.app.state.credit_executor,
        lambda: read_credits(settings, context="credits"),
    )
    if report.credits is None:
        return CreditsResponse(credits=None, error=report.error)
    return CreditsResponse(
        credits=CreditsRecord.from_usage(report.credits), error=None
    )
