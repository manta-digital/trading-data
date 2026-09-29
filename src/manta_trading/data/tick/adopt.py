"""``mt data tick adopt``: a batch job already on disk → archive → manifest (223).

LLD 224 Technical Decision 10 and the Adoption data flow:

1. a job id already in ``tick_request`` → "already adopted"; nothing written;
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
named).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg

from manta_trading.data.base.trading_calendar import (
    CalendarNotFoundError,
    OutOfPopulatedRangeError,
    TradingCalendar,
)
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
    SType,
    UnitState,
    calendar_for_product,
)
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
from manta_trading.data.tick.session_days import session_days
from manta_trading.data.tick.verify import check
from manta_trading.market.schema.databases import (
    Credential,
    Database,
    DatabaseNotConfiguredError,
    resolve_database_url,
)

#: Job states whose files exist (or existed) and whose record is final.
ADOPTABLE_STATES = frozenset({BatchJobState.DONE, BatchJobState.EXPIRED})
#: Symbologies whose symbols begin with the product root (``ES.FUT``, ``ES.c.0``).
_ROOTED_STYPES = frozenset({SType.PARENT, SType.CONTINUOUS})
_ROOT_SEPARATOR = "."
#: Provider data-file suffixes; every other listed file is job metadata.
DATA_FILE_SUFFIXES = (".dbn.zst", ".dbn")


class TickCalendarError(Exception):
    """The calendar could not give session days: nothing written (exit 4)."""


@dataclass(frozen=True)
class AdoptResult:
    """What ``adopt`` did, for rendering."""

    job_id: str
    already_adopted: bool
    cost_usd: Decimal | None = None
    files: tuple[ArchivedFile, ...] = ()
    units_by_state: dict[str, int] = field(default_factory=dict)
    holes: tuple[date, ...] = ()
    strays: tuple[str, ...] = ()
    verify_failures: tuple[str, ...] = ()


def product_of(job: BatchJob) -> str:
    """The futures product every symbol of the job belongs to."""
    request = job.request
    if request.stype_in not in _ROOTED_STYPES:
        raise TickAdoptionRefused(
            f"job {job.job_id}: stype_in {request.stype_in} does not name a product"
        )
    roots = {symbol.split(_ROOT_SEPARATOR, 1)[0] for symbol in request.symbols}
    if len(roots) != 1:
        raise TickAdoptionRefused(f"job {job.job_id} spans products {sorted(roots)}")
    return roots.pop()


def _session_days_blocking(url: str, job: BatchJob) -> list[date]:
    calendar_id = calendar_for_product(product_of(job))
    calendar = TradingCalendar(calendar_id, url)
    try:
        return session_days(calendar, job.request.start, job.request.end)
    except (psycopg.OperationalError, CalendarNotFoundError) as exc:
        raise TickCalendarError(f"calendar {calendar_id} unavailable: {exc}") from exc
    except OutOfPopulatedRangeError as exc:
        raise TickCalendarError(f"calendar {calendar_id}: {exc}") from exc
    finally:
        calendar.close()


async def _job(run: TickRun, job_id: str) -> BatchJob:
    job = await asyncio.to_thread(run.provider.batch_job, job_id)
    if job.state not in ADOPTABLE_STATES:
        raise TickAdoptionRefused(
            f"job {job_id} is {job.state}; only done or expired jobs are adopted"
        )
    if job.cost_usd is None:
        raise TickAdoptionRefused(f"job {job_id} reports no cost")
    return job


def _files_by_day(
    run: TickRun, job_id: str, files: list[ArchivedFile], reader: ITickFileReader
) -> dict[date, tuple[ArchivedFile, str]]:
    """Data files keyed by the UTC day their header starts on. Blocking."""
    by_day: dict[date, tuple[ArchivedFile, str]] = {}
    for archived in files:
        if not archived.name.endswith(DATA_FILE_SUFFIXES):
            continue
        rel_path = f"{job_id}/{archived.name}"
        day = reader.open_file(run.archive_root / rel_path).start.date()
        if day in by_day:
            raise TickAdoptionRefused(
                f"{archived.name} and {by_day[day][0].name} both start on {day}"
            )
        by_day[day] = (archived, rel_path)
    return by_day


def _units(
    days: list[date], by_day: dict[date, tuple[ArchivedFile, str]]
) -> list[NewUnit]:
    units = []
    for day in days:
        if day in by_day:
            archived, rel_path = by_day[day]
            unit_file = UnitFile(rel_path, archived.size, archived.sha256)
            units.append(NewUnit(day, UnitState.DOWNLOADED, file=unit_file))
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


async def adopt_job(
    run: TickRun,
    job_id: str,
    source: Path,
    reader: ITickFileReader,
    free: FreeBytes = free_bytes,
) -> AdoptResult:
    """Adopt one job; idempotent by job id."""
    if await request_id_for_job(run.conn, job_id) is not None:
        return AdoptResult(job_id, already_adopted=True)
    calendar_url = _calendar_url(run)
    job = await _job(run, job_id)
    # Calendar before copy: a calendar refusal leaves no files in the archive.
    days = await asyncio.to_thread(_session_days_blocking, calendar_url, job)
    files = await asyncio.to_thread(
        archive_job_files, source, job_id, run.archive_root, free
    )
    by_day = await asyncio.to_thread(_files_by_day, run, job_id, files, reader)
    wanted = set(days)
    units = _units(days, by_day)
    request_id = await _insert(run, job, units)
    failures = []
    for unit in await units_of_request(run.conn, request_id):
        if unit.state is UnitState.DOWNLOADED:
            outcome = await check(run, unit, reader)
            if outcome.failure is not None:
                failures.append(f"{unit.unit_date}: {outcome.failure}")
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
        strays=tuple(sorted(f.name for d, (f, _) in by_day.items() if d not in wanted)),
        verify_failures=tuple(failures),
    )


async def _insert(run: TickRun, job: BatchJob, units: list[NewUnit]) -> int:
    await insert_adopted_request(run.conn, _adopted_request(job), units, run.clock())
    request_id = await request_id_for_job(run.conn, job.job_id)
    assert request_id is not None  # inserted just above
    return request_id
