"""``mt data tick adopt``: a batch job already on disk → archive → manifest (223).

LLD 224 Technical Decision 10 and the Adoption data flow:

1. a job id already in ``tick_request`` → "already adopted": only units a
   previous run left *downloaded* are verified (step 6), nothing else;
2. ``batch_job(ID)`` (free) must be ``done`` or ``expired``; its request,
   cost, record count and ``ts_received`` are the facts recorded;
3. the product's calendar, read from the production database (TD6), gives
   the session days of the job's range;
4. ``adopt_files`` copies and verifies every listed file;
5. one transaction: the adopted request and one unit per session day — a day
   whose file header starts on it → *downloaded*, a session day with no file
   → *delivered* + ``PROVIDER_HOLE``; a file on no session day is reported by
   name and gets no unit;
6. ``verify.check`` on each downloaded unit.

Refusals before step 5 write no row; a calendar refusal also copies no file.
Calendar failures raise :class:`TickCalendarError` (exit 4, the calendar
named). A ``ProviderError`` during step 6 leaves the rest *downloaded* and
raises :class:`TickVerifyInterrupted` (exit 2), which says to run the same
command again.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.adopt_files import (
    ArchivedFile,
    FreeBytes,
    TickAdoptionRefused,
    archive_job_files,
    free_bytes,
)
from manta_trading.data.tick.constants import (
    BatchJobState,
    UnitState,
)
from manta_trading.data.tick.file_days import DuplicateDayError, FileDays, file_days
from manta_trading.data.tick.manifest_reads import (
    UnitFile,
    request_id_for_job,
    units_of_request,
)
from manta_trading.data.tick.manifest_repo import (
    PROVIDER_HOLE_REASON,
    AdoptedRequest,
    NewUnit,
    insert_adopted_request,
)
from manta_trading.data.tick.provider import BatchJob, ITickFileReader
from manta_trading.data.tick.run_context import TickRun
from manta_trading.data.tick.tick_calendar import (
    product_of_shape,
    product_session_days,
)
from manta_trading.data.tick.verify import check
from manta_trading.market.schema.databases import (
    Credential,
    Database,
    DatabaseNotConfiguredError,
    resolve_database_url,
)
from manta_trading.providers.errors import ProviderError

#: Job states whose files exist (or existed) and whose record is final.
ADOPTABLE_STATES = frozenset({BatchJobState.DONE, BatchJobState.EXPIRED})


class TickVerifyInterrupted(Exception):
    """The provider failed mid-verification; re-running adopt resumes (exit 2)."""


@dataclass(frozen=True)
class AdoptResult:
    """What ``adopt`` did, for rendering."""

    job_id: str
    already_adopted: bool
    reverified: int = 0
    cost_usd: Decimal | None = None
    files: tuple[ArchivedFile, ...] = ()
    units_by_state: dict[str, int] = field(default_factory=dict)
    holes: tuple[date, ...] = ()
    strays: tuple[str, ...] = ()
    verify_failures: tuple[str, ...] = ()


def product_of(job: BatchJob) -> str:
    """The futures product every symbol of the job belongs to."""
    request = job.request
    try:
        return product_of_shape(request.stype_in, request.symbols)
    except ValueError as exc:
        raise TickAdoptionRefused(f"job {job.job_id}: {exc}") from exc


def _session_days_blocking(url: str, job: BatchJob) -> list[date]:
    request = job.request
    return product_session_days(url, product_of(job), request.start, request.end)


async def _job(run: TickRun, job_id: str) -> BatchJob:
    job = await asyncio.to_thread(run.provider.batch_job, job_id)
    if job.state not in ADOPTABLE_STATES:
        raise TickAdoptionRefused(
            f"job {job_id} is {job.state}; only done or expired jobs are adopted"
        )
    if job.cost_usd is None:
        raise TickAdoptionRefused(f"job {job_id} reports no cost")
    return job


@dataclass(frozen=True)
class _JobDays:
    by_day: dict[date, tuple[ArchivedFile, str]]
    found: FileDays


def _files_by_day(
    run: TickRun, job_id: str, files: list[ArchivedFile], reader: ITickFileReader
) -> _JobDays:
    """Data files keyed by the UTC day their header starts on. Blocking."""
    archived = {f"{job_id}/{f.name}": f for f in files}
    try:
        found = file_days((run.archive_root / rel for rel in archived), reader)
    except DuplicateDayError as exc:
        raise TickAdoptionRefused(str(exc)) from exc
    by_day: dict[date, tuple[ArchivedFile, str]] = {}
    for day, path in found.by_day.items():
        rel = f"{job_id}/{path.name}"
        by_day[day] = (archived[rel], rel)
    return _JobDays(by_day, found)


def _units(days: list[date], job: _JobDays) -> list[NewUnit]:
    units = []
    for day in days:
        if day in job.by_day:
            archived, rel_path = job.by_day[day]
            unit_file = UnitFile(rel_path, archived.size, archived.sha256)
            units.append(NewUnit(day, UnitState.DOWNLOADED, file=unit_file))
        elif job.found.unreadable:
            # A refused header may be this day's file: a failure, not a hole.
            reason = job.found.unclaimed_reason()
            units.append(
                NewUnit(day, UnitState.DELIVERED, FetchStatus.RETRY_EXHAUSTED, reason)
            )
        else:
            units.append(
                NewUnit(
                    day,
                    UnitState.DELIVERED,
                    FetchStatus.PROVIDER_HOLE,
                    PROVIDER_HOLE_REASON,
                )
            )
    return units


def _adopted_request(job: BatchJob) -> AdoptedRequest:
    assert job.cost_usd is not None  # checked by _job
    return AdoptedRequest(
        job_id=job.job_id,
        dataset=job.request.dataset,
        schema=job.request.schema,
        symbols=job.request.symbols,
        stype_in=job.request.stype_in,
        range_start=job.request.start,
        range_end=job.request.end,
        cost_usd=job.cost_usd,
        record_count=job.record_count,
        billed_size=job.billed_size,
        ts_received=job.ts_received,
    )


def _calendar_url(run: TickRun) -> str:
    try:
        return resolve_database_url(
            run.settings, Database.PRIMARY, Credential.APPLICATION
        )
    except DatabaseNotConfiguredError as exc:
        raise TickAdoptionRefused(f"{exc.env_var} (the calendar) is not set") from exc


async def _verify_downloaded(
    run: TickRun, job_id: str, request_id: int, reader: ITickFileReader
) -> tuple[int, tuple[str, ...]]:
    """Verify the request's *downloaded* units: (how many checked, failures)."""
    pending = [
        u
        for u in await units_of_request(run.conn, request_id)
        if u.state is UnitState.DOWNLOADED
    ]
    failures = []
    for done, unit in enumerate(pending):
        try:
            outcome = await check(run, unit, reader)
        except ProviderError as exc:
            raise TickVerifyInterrupted(
                f"job {job_id}: provider error while verifying {unit.unit_date}: "
                f"{exc}; {len(pending) - done} unit(s) left unverified. Run the "
                "same adopt command again to verify them."
            ) from exc
        if outcome.failure is not None:
            failures.append(f"{unit.unit_date}: {outcome.failure}")
    return len(pending), tuple(failures)


async def adopt_job(
    run: TickRun,
    job_id: str,
    source: Path,
    reader: ITickFileReader,
    free: FreeBytes = free_bytes,
) -> AdoptResult:
    """Adopt one job; idempotent by job id, and resumes an interrupted verify."""
    existing = await request_id_for_job(run.conn, job_id)
    if existing is not None:
        checked, failures = await _verify_downloaded(run, job_id, existing, reader)
        return AdoptResult(
            job_id, already_adopted=True, reverified=checked, verify_failures=failures
        )
    calendar_url = _calendar_url(run)
    job = await _job(run, job_id)
    # Calendar before copy: a calendar refusal leaves no files in the archive.
    days = await asyncio.to_thread(_session_days_blocking, calendar_url, job)
    files = await asyncio.to_thread(
        archive_job_files, source, job_id, run.archive_root, free
    )
    job_days = await asyncio.to_thread(_files_by_day, run, job_id, files, reader)
    wanted = set(days)
    request_id = await _insert(run, job, _units(days, job_days))
    _, verify_failures = await _verify_downloaded(run, job_id, request_id, reader)
    failures = tuple(f"header: {line}" for line in job_days.found.unreadable)
    failures += verify_failures
    final = await units_of_request(run.conn, request_id)
    return AdoptResult(
        job_id=job_id,
        already_adopted=False,
        cost_usd=job.cost_usd,
        files=tuple(files),
        units_by_state=dict(Counter(u.state.value for u in final)),
        holes=tuple(
            u.unit_date for u in final if u.fetch_status is FetchStatus.PROVIDER_HOLE
        ),
        strays=tuple(
            sorted(f.name for d, (f, _) in job_days.by_day.items() if d not in wanted)
        ),
        verify_failures=failures,
    )


async def _insert(run: TickRun, job: BatchJob, units: list[NewUnit]) -> int:
    await insert_adopted_request(run.conn, _adopted_request(job), units, run.clock())
    request_id = await request_id_for_job(run.conn, job.job_id)
    assert request_id is not None  # inserted just above
    return request_id
