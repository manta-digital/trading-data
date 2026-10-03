"""``jobs``: every account batch job's submit → done time (LLD 226 TD3).

Free metadata calls only (``ITickMetadataProvider.batch_jobs_since``). The
slowest time feeds the wait-budget rule: max(1800 s, 2 × slowest job).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_WAIT_BUDGET_SECONDS
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.provider import BatchJob
from proof_226.common import Report

#: Before the account's first futures job (GLBX-20240930-…). The listing is
#: free; the three XNAS jobs of 2024-07-26 before it are an equities
#: experiment with ranges off a UTC day boundary, which the job parser
#: refuses by design (no request this system makes has one).
LISTING_SINCE = datetime(2024, 9, 1, tzinfo=UTC)
#: The wait-budget rule (TD3): this multiple of the slowest job, with the
#: current budget as the floor.
WAIT_BUDGET_MULTIPLE = 2


def job_seconds(job: BatchJob) -> float | None:
    """Submit → done, or ``None`` for a job the provider has not finished."""
    if job.ts_process_done is None:
        return None
    return (job.ts_process_done - job.ts_received).total_seconds()


def wait_budget(slowest_seconds: float) -> int:
    return max(TICK_WAIT_BUDGET_SECONDS, round(WAIT_BUDGET_MULTIPLE * slowest_seconds))


def run() -> Path:
    report = Report("jobs", "Proof: batch job submit → done times (slice 226)")
    with DatabentoTickProvider.from_settings(Settings()) as provider:
        jobs = provider.batch_jobs_since(LISTING_SINCE)
    timed = sorted(
        ((job, s) for job in jobs if (s := job_seconds(job)) is not None),
        key=lambda pair: pair[1],
    )
    report.add("## Jobs", "")
    report.table(
        ("job", "schema", "range", "state", "records", "submit → done (s)"),
        [
            (
                job.job_id,
                job.request.schema,
                f"{job.request.start} → {job.request.end}",
                job.state,
                job.record_count,
                "not done" if (s := job_seconds(job)) is None else f"{s:.0f}",
            )
            for job in sorted(jobs, key=lambda j: j.ts_received)
        ],
    )
    if not timed:
        report.add("No finished job: the wait-budget rule has no input.")
        return report.write()
    (fastest, low), (slowest, high) = timed[0], timed[-1]
    report.add(
        "## Result",
        "",
        f"- fastest: {fastest.job_id} {low:.0f} s",
        f"- slowest: {slowest.job_id} {high:.0f} s",
        f"- wait budget by the rule max({TICK_WAIT_BUDGET_SECONDS}, "
        f"{WAIT_BUDGET_MULTIPLE} × {high:.0f}) = **{wait_budget(high)} s** "
        f"(current {TICK_WAIT_BUDGET_SECONDS} s)",
    )
    return report.write()
