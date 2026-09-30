"""The purchase phase: submit what the guards allow (224).

LLD 224 TD7–TD9. :func:`~manta_trading.data.tick.purchase_plan.build_plan`
does all the free work; this module is the only place the pass spends. The
only paid method it calls is ``submit_batch``, and only after both guards
allow it — nothing calls ``fetch_range`` (PM direction: batch jobs only).

Per request, in plan order: write the request and its units at *requested*
with the attempt stamped (or stamp a ``reset`` row again), call
``submit_batch``, then record the accepted job. Outcomes (TD9):

* accepted → the job id and the provider's ``ts_received`` are recorded;
* ``ProviderOutcomeUnknownError`` → units ``FAILED_RETRYABLE``, stop
  submitting, ``provider_abort`` (the attempt was already counted);
* 429 or an auth refusal → units ``FAILED_RETRYABLE``, ``provider_abort``;
* any other 4xx → units ``RETRY_EXHAUSTED``, continue, ``partial``.

A guard refusal with wants ends ``refused``; ``--estimate-only`` and nothing
to buy end ``ok``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.adopt_files import FreeBytes, free_bytes
from manta_trading.data.tick.manifest_pass import (
    insert_pending_request,
    mark_submit_outcome,
    record_submit,
    restamp_attempt,
)
from manta_trading.data.tick.manifest_request_reads import (
    reopened_unit_ids,
)
from manta_trading.data.tick.pass_contract import (
    PhaseReport,
    TickOutcome,
    TickPassPhaseName,
)
from manta_trading.data.tick.phase_support import run_phase
from manta_trading.data.tick.purchase_plan import (
    PlanItem,
    PurchasePlan,
    Window,
    build_plan,
    plan_summary,
)
from manta_trading.data.tick.run_context import TickRun
from manta_trading.logging import get_logger
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderOutcomeUnknownError,
    ProviderPermanentError,
    ProviderTransientError,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class SubmittedJob:
    job_id: str
    requested_at: datetime


@dataclass
class PassState:
    """What the purchase phase hands the await phase, within one pass."""

    submitted: list[SubmittedJob] = field(default_factory=list)


async def _stamp(run: TickRun, item: PlanItem) -> int:
    """The request row a submit will be recorded on, its attempt stamped first."""
    now = run.clock()
    if item.resubmit_of is not None:
        await restamp_attempt(run.conn, item.resubmit_of, now)
        return item.resubmit_of
    request = item.planned.request
    links = await reopened_unit_ids(run.conn, request, list(item.planned.days))
    pending = await insert_pending_request(
        run.conn, item.planned, item.cost, now, links
    )
    return pending.request_id


async def _submit_one(
    run: TickRun, item: PlanItem, state: PassState, submitted: list[dict[str, Any]]
) -> TickOutcome:
    """Submit one request; ``OK`` or ``PARTIAL``. A run-level refusal raises."""
    request_id = await _stamp(run, item)
    requested_at = run.clock()
    conn = run.conn
    try:
        job = await asyncio.to_thread(run.provider.submit_batch, item.planned.request)
    except ProviderOutcomeUnknownError as exc:
        await mark_submit_outcome(
            conn,
            request_id,
            FetchStatus.FAILED_RETRYABLE,
            f"submit outcome unknown: {exc}",
        )
        raise
    except (ProviderTransientError, ProviderAuthError) as exc:
        await mark_submit_outcome(
            conn, request_id, FetchStatus.FAILED_RETRYABLE, f"submit refused: {exc}"
        )
        raise
    except ProviderPermanentError as exc:
        await mark_submit_outcome(
            conn, request_id, FetchStatus.RETRY_EXHAUSTED, f"submit refused: {exc}"
        )
        logger.warning("tick request %s refused by the provider: %s", request_id, exc)
        return TickOutcome.PARTIAL
    await record_submit(conn, request_id, job.job_id, job.ts_received, run.clock())
    state.submitted.append(SubmittedJob(job.job_id, requested_at))
    submitted.append({"job_id": job.job_id, "request_id": request_id})
    logger.info("tick job %s submitted for request %s", job.job_id, request_id)
    return TickOutcome.OK


async def _submit_all(
    run: TickRun, purchase: PurchasePlan, state: PassState, summary: dict[str, Any]
) -> TickOutcome:
    submitted: list[dict[str, Any]] = []
    summary["submitted"] = submitted
    outcome = TickOutcome.OK
    for item in purchase.items:
        if await _submit_one(run, item, state, submitted) is TickOutcome.PARTIAL:
            outcome = TickOutcome.PARTIAL
    return outcome


class PurchasePhase:
    """Plan, guard and submit; see the module docstring."""

    name = TickPassPhaseName.PURCHASE

    def __init__(
        self,
        window: Window,
        estimate_only: bool,
        state: PassState,
        free: FreeBytes = free_bytes,
    ) -> None:
        self._window = window
        self._estimate_only = estimate_only
        self._state = state
        self._free = free

    async def run(self, run: TickRun) -> PhaseReport:
        async def body(summary: dict[str, Any]) -> TickOutcome:
            purchase = await build_plan(
                run, self._window, self._estimate_only, self._free
            )
            summary.update(plan_summary(purchase))
            if not purchase.has_wants or self._estimate_only:
                return TickOutcome.OK
            assert purchase.spend is not None and purchase.space is not None
            if not (purchase.spend.allowed and purchase.space.allowed):
                return TickOutcome.REFUSED
            return await _submit_all(run, purchase, self._state, summary)

        return await run_phase(self.name, body)
