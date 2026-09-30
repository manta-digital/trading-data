"""In-flight work: resolve unknown submits, sweep expiry, advance deliveries (224).

LLD 224 Technical Decisions 8 and 9. Three entry points, all shared by the
reconcile and await phases so there is one path for delivery:

* :func:`resolve_unsubmitted` — a request with no job id was, or may have been,
  submitted. Search the provider's job list for an exact match no row holds; a
  match is recorded (it was bought once). No match is never proof that nothing
  was bought: the row stays ``FAILED_RETRYABLE`` while the attempt is younger
  than ``TICK_SUBMIT_RESOLVE_AGE``, then ``RETRY_EXHAUSTED``, and only
  ``reset`` lets the purchase phase submit it again. **Nothing here submits.**
* :func:`sweep_expired` — units whose request deadline passed without a file
  are exhausted and reopened so the planner wants the day again.
* :func:`advance` — poll each submitted job (done → delivered, expired →
  exhausted and reopened, an unknown state → exhausted naming it), download
  delivered jobs earliest deadline first, then verify every downloaded unit.
  ``ProviderError`` (a failed download or record-count call) propagates: the
  phase aborts, and unverified units stay *downloaded* for the next run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    TICK_JOB_MATCH_SKEW,
    TICK_SUBMIT_RESOLVE_AGE,
    BatchJobState,
    UnitState,
)
from manta_trading.data.tick.in_flight_files import deliver_job
from manta_trading.data.tick.manifest_pass import (
    expire_units,
    mark_delivered,
    mark_submit_outcome,
    record_submit,
)
from manta_trading.data.tick.manifest_reads import (
    UnitRow,
    open_units_in,
    request_id_for_job,
    units_of_request,
)
from manta_trading.data.tick.manifest_repo import record_failure
from manta_trading.data.tick.manifest_request_reads import (
    RequestRow,
    jobless_requests,
    requests_with_units_in,
    swept_candidates,
)
from manta_trading.data.tick.provider import BatchJob, ITickFileReader
from manta_trading.data.tick.run_context import TickRun
from manta_trading.data.tick.verify import check
from manta_trading.logging import get_logger
from manta_trading.providers.errors import ProviderPermanentError

logger = get_logger(__name__)

UNRESOLVED_REASON = "submit outcome unresolved"
EXHAUSTED_REASON = (
    "submit outcome unknown; no job listed after "
    f"{TICK_SUBMIT_RESOLVE_AGE}; `mt data tick reset` to re-submit"
)


@dataclass(frozen=True)
class InFlightJob:
    """A job the provider has not finished, for the closing report."""

    job_id: str
    state: str
    deadline: datetime | None


@dataclass
class ResolveTally:
    matched: int = 0
    retryable: int = 0
    exhausted: int = 0


@dataclass
class AdvanceTally:
    """What one ``advance`` did; ``to_dict`` is the phase summary."""

    polled: int = 0
    delivered: int = 0
    expired: int = 0
    refused: int = 0
    downloaded: int = 0
    verified: int = 0
    holed: int = 0
    failed: int = 0
    strays: list[str] = field(default_factory=list)
    in_flight: list[InFlightJob] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Every integer counter by field name — the one list of them."""
        values = {f.name: getattr(self, f.name) for f in fields(self)}
        return {name: v for name, v in values.items() if isinstance(v, int)}

    def add(self, other: AdvanceTally) -> None:
        """Fold a later poll into this total; ``in_flight`` is the latest poll's."""
        for name, value in other.counts().items():
            setattr(self, name, getattr(self, name) + value)
        self.strays.extend(other.strays)
        self.in_flight = list(other.in_flight)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.counts(),
            "strays": list(self.strays),
            "in_flight": [
                {
                    "job_id": job.job_id,
                    "state": job.state,
                    "deadline": job.deadline.isoformat() if job.deadline else None,
                }
                for job in self.in_flight
            ],
        }


# -- unknown submits ---------------------------------------------------------------


async def _match(
    run: TickRun, row: RequestRow, listed: tuple[BatchJob, ...]
) -> BatchJob | None:
    """The earliest listed job that is exactly this request and that no row holds."""
    earliest = row.requested_at - TICK_JOB_MATCH_SKEW
    for job in sorted(listed, key=lambda j: j.ts_received):
        if job.request != row.request or job.ts_received < earliest:
            continue
        if await request_id_for_job(run.conn, job.job_id) is None:
            return job
    return None


async def _leave_or_age(
    run: TickRun, row: RequestRow, units: list[UnitRow], tally: ResolveTally
) -> None:
    """No job found: age the row's units toward exhausted; never submit."""
    if any(unit.attempt_count == 0 for unit in units):
        return  # after `reset`: the purchase phase submits it, having searched
    attempted = min(u.last_attempt_at for u in units if u.last_attempt_at is not None)
    if run.clock() - attempted < TICK_SUBMIT_RESOLVE_AGE:
        await mark_submit_outcome(
            run.conn, row.request_id, FetchStatus.FAILED_RETRYABLE, UNRESOLVED_REASON
        )
        tally.retryable += 1
    else:
        await mark_submit_outcome(
            run.conn, row.request_id, FetchStatus.RETRY_EXHAUSTED, EXHAUSTED_REASON
        )
        tally.exhausted += 1


async def resolve_unsubmitted(run: TickRun) -> ResolveTally:
    """Match each jobless request to a listed job, or age it (TD9)."""
    tally = ResolveTally()
    rows = await jobless_requests(run.conn)
    if not rows:
        return tally
    since = min(row.requested_at for row in rows) - TICK_JOB_MATCH_SKEW
    listed = await asyncio.to_thread(run.provider.batch_jobs_since, since)
    for row in rows:
        units = [
            u
            for u in await units_of_request(run.conn, row.request_id)
            if u.state is UnitState.REQUESTED and u.reopened_at is None
        ]
        job = await _match(run, row, listed)
        if job is not None:
            await record_submit(
                run.conn, row.request_id, job.job_id, job.ts_received, run.clock()
            )
            logger.info("tick request %s matched job %s", row.request_id, job.job_id)
            tally.matched += 1
        elif not all(u.fetch_status is FetchStatus.RETRY_EXHAUSTED for u in units):
            await _leave_or_age(run, row, units, tally)
    return tally


# -- expiry ------------------------------------------------------------------------


async def sweep_expired(run: TickRun) -> int:
    """Exhaust and reopen units whose retention deadline passed with no file."""
    swept = 0
    for deadline, unit_ids in (await swept_candidates(run.conn, run.clock())).items():
        swept += await expire_units(
            run.conn,
            unit_ids,
            f"retention expired at {deadline.isoformat()}",
            run.clock(),
        )
    return swept


# -- advance -----------------------------------------------------------------------


async def _exhaust_submitted(run: TickRun, request: RequestRow, reason: str) -> None:
    for unit in await units_of_request(run.conn, request.request_id):
        if unit.state is UnitState.SUBMITTED and unit.reopened_at is None:
            await record_failure(
                run.conn,
                unit.unit_id,
                UnitState.SUBMITTED,
                reason,
                run.clock(),
                deterministic=True,
            )


async def _poll(run: TickRun, request: RequestRow, tally: AdvanceTally) -> None:
    assert request.job_id is not None
    try:
        job = await asyncio.to_thread(run.provider.batch_job, request.job_id)
    except ProviderPermanentError as exc:
        await _exhaust_submitted(run, request, f"job {request.job_id}: {exc}")
        tally.refused += 1
        return
    tally.polled += 1
    if job.state is BatchJobState.DONE:
        if job.cost_usd is None:
            await _exhaust_submitted(
                run, request, f"job {job.job_id} is done but reports no cost"
            )
            tally.refused += 1
            return
        await mark_delivered(
            run.conn,
            request.request_id,
            cost=job.cost_usd,
            record_count=job.record_count,
            billed_size=job.billed_size,
            deadline=job.ts_expiration,
            now=run.clock(),
        )
        tally.delivered += 1
    elif job.state is BatchJobState.EXPIRED:
        units = await units_of_request(run.conn, request.request_id)
        ids = [u.unit_id for u in units if u.state is UnitState.SUBMITTED]
        await expire_units(run.conn, ids, f"job {job.job_id} expired", run.clock())
        tally.expired += 1
    else:
        tally.in_flight.append(
            InFlightJob(job.job_id, job.state.value, job.ts_expiration)
        )


async def advance(run: TickRun, reader: ITickFileReader) -> AdvanceTally:
    """One pass over the in-flight work; see the module docstring."""
    tally = AdvanceTally()
    for request in await requests_with_units_in(run.conn, UnitState.SUBMITTED):
        await _poll(run, request, tally)
    for request in await requests_with_units_in(run.conn, UnitState.DELIVERED):
        delivery = await deliver_job(run, request, reader)
        tally.downloaded += delivery.downloaded
        tally.holed += delivery.holed
        tally.strays.extend(delivery.strays)
    for unit in await open_units_in(run.conn, UnitState.DOWNLOADED):
        outcome = await check(run, unit, reader)
        if outcome.failure is None:
            tally.verified += 1
        else:
            tally.failed += 1
    return tally


async def log_listing_lag(run: TickRun, submitted: list[tuple[str, datetime]]) -> None:
    """Log whether ``batch_jobs_since`` lists each just-submitted job on its
    first poll (the listing-lag measurement, LLD Risk Assessment)."""
    if not submitted:
        return
    since = min(at for _, at in submitted) - TICK_JOB_MATCH_SKEW
    listed = {
        j.job_id for j in await asyncio.to_thread(run.provider.batch_jobs_since, since)
    }
    for job_id, _ in submitted:
        logger.info(
            "tick job %s listed on first poll after submit: %s",
            job_id,
            job_id in listed,
        )
