"""A stateful in-memory tick provider for the acquisition pass tests (slice 224).

Implements the whole ``TickProvider`` surface (free metadata, acquisition and
context manager) without the SDK, so a test decides exactly what the provider
does: what a submit answers, when a job finishes, which day files a download
delivers. Every call is recorded in order in ``calls``. ``fetch_range`` is
paid and the pass never calls it, so the fake fails the test if it is.

Job files are real DBN day files (re-headed fixtures) written to disk on
download, plus the provider's JSON files and, unless ``supply_manifest`` is
off, ``manifest.json``.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Self

from tick_support.dbn_files import (
    CME_DEFINITION_RECORDS,
    JOB_JSON_FILES,
    MANIFEST_NAME,
    day_file_bytes,
    definition_file_bytes,
)

from manta_trading.data.tick.constants import (
    CME_DATASET,
    BatchJobState,
    DatasetCondition,
    SType,
    TickSchema,
)
from manta_trading.data.tick.provider import (
    BatchJob,
    DatasetRange,
    DayCondition,
    SymbolResolution,
    TickRequest,
)
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderOutcomeUnknownError,
    ProviderPermanentError,
    ProviderTransientError,
)

RETENTION = timedelta(days=30)
FIXTURE_FOR_SCHEMA = {
    TickSchema.TRADES: "test_data.trades.v3.dbn.zst",
    TickSchema.TBBO: "test_data.tbbo.v3.dbn.zst",
    TickSchema.DEFINITION: "test_data.definition.v3.dbn.zst",
}
DEFAULT_EDGE = DatasetRange(
    datetime(2010, 6, 6, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC)
)


class Submit:
    """What the next ``submit_batch`` does (the ``submit_script`` entries)."""

    ACCEPT = "accept"
    UNKNOWN_ACCEPTED = "unknown_accepted"  # raises unknown; the job exists
    UNKNOWN_LOST = "unknown_lost"  # raises unknown; nothing was bought
    REFUSE = "refuse"  # a 4xx: ProviderPermanentError
    RATE_LIMIT = "rate_limit"  # 429: ProviderTransientError
    AUTH = "auth"  # 401/403: ProviderAuthError


class FakeClock:
    """A settable clock; the run's ``clock`` and the fake's share it."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, span: timedelta) -> None:
        self.now += span


@dataclass
class FakeJob:
    job_id: str
    request: TickRequest
    state: BatchJobState
    ts_received: datetime
    cost_usd: Decimal
    listed: bool = True
    polls_until_done: int | None = 0
    unknown_state: str | None = None


@dataclass(frozen=True)
class Call:
    method: str
    subject: object


@dataclass
class FakeTickProvider:
    clock: Callable[[], datetime]
    edge: DatasetRange = DEFAULT_EDGE
    condition_of: Callable[[date], DatasetCondition] = lambda _day: (
        DatasetCondition.AVAILABLE
    )
    unit_cost: Decimal = Decimal("0.001")
    day_bytes: int = 1000
    records_per_day: int = 3
    initial_state: BatchJobState = BatchJobState.DONE
    polls_until_done: int | None = 0  # None: a job never leaves processing
    supply_manifest: bool = True
    missing_days: set[date] = field(default_factory=set)
    submit_script: deque[str] = field(default_factory=deque)
    download_errors: deque[BaseException] = field(default_factory=deque)
    record_count_error: BaseException | None = None
    jobs: dict[str, FakeJob] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    _next_id: int = 1

    # -- context manager (TickProvider) -------------------------------------
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        /,
    ) -> None:
        return None

    # -- recording -----------------------------------------------------------
    def calls_to(self, method: str) -> list[object]:
        return [call.subject for call in self.calls if call.method == method]

    def _record(self, method: str, subject: object = None) -> None:
        self.calls.append(Call(method, subject))

    # -- free metadata -------------------------------------------------------
    def dataset_range(self, dataset: str) -> DatasetRange:
        self._record("dataset_range", dataset)
        return self.edge

    def dataset_condition(
        self, dataset: str, start: date, end: date
    ) -> tuple[DayCondition, ...]:
        self._record("dataset_condition", (dataset, start, end))
        days = (start + timedelta(days=n) for n in range((end - start).days))
        return tuple(
            DayCondition(day, self.condition_of(day), day + timedelta(days=1))
            for day in days
        )

    def record_count(self, request: TickRequest) -> int:
        self._record("record_count", request)
        if self.record_count_error is not None:
            raise self.record_count_error
        return self.records_per_day * (request.end - request.start).days

    def billable_size(self, request: TickRequest) -> int:
        self._record("billable_size", request)
        return self.day_bytes * (request.end - request.start).days

    def cost(self, request: TickRequest) -> Decimal:
        self._record("cost", request)
        return self.unit_cost * (request.end - request.start).days

    def resolve_symbols(self, request: TickRequest) -> SymbolResolution:
        raise NotImplementedError("the pass never resolves symbols")

    # -- acquisition -----------------------------------------------------------
    def fetch_range(self, request: TickRequest, dest: Path) -> Path:
        raise AssertionError("fetch_range is paid; the pass must never call it")

    def _snapshot(self, job: FakeJob) -> BatchJob:
        if job.unknown_state is not None:
            raise ProviderPermanentError(f"unknown job state {job.unknown_state!r}")
        priced = job.state in {BatchJobState.DONE, BatchJobState.EXPIRED}
        return BatchJob(
            job_id=job.job_id,
            request=job.request,
            state=job.state,
            ts_received=job.ts_received,
            ts_expiration=job.ts_received + RETENTION,
            ts_process_done=job.ts_received if priced else None,
            record_count=self._records(job) if priced else None,
            billed_size=self.day_bytes if priced else None,
            actual_size=self.day_bytes if priced else None,
            package_size=self.day_bytes if priced else None,
            cost_usd=job.cost_usd if priced else None,
        )

    def _records(self, job: FakeJob) -> int:
        return self.records_per_day * (job.request.end - job.request.start).days

    def add_job(
        self,
        request: TickRequest,
        *,
        state: BatchJobState | None = None,
        listed: bool = True,
        cost: Decimal | None = None,
        ts_received: datetime | None = None,
    ) -> FakeJob:
        job = FakeJob(
            job_id=f"GLBX-FAKE-{self._next_id:04d}",
            request=request,
            state=state or self.initial_state,
            ts_received=ts_received or self.clock(),
            cost_usd=cost
            if cost is not None
            else self.unit_cost * ((request.end - request.start).days),
            listed=listed,
            polls_until_done=self.polls_until_done,
        )
        self._next_id += 1
        self.jobs[job.job_id] = job
        return job

    def submit_batch(self, request: TickRequest) -> BatchJob:
        self._record("submit_batch", request)
        outcome = self.submit_script.popleft() if self.submit_script else Submit.ACCEPT
        if outcome == Submit.REFUSE:
            raise ProviderPermanentError("submit refused: 422")
        if outcome == Submit.RATE_LIMIT:
            raise ProviderTransientError("submit refused: 429")
        if outcome == Submit.AUTH:
            raise ProviderAuthError("submit refused: 401")
        if outcome == Submit.UNKNOWN_LOST:
            raise ProviderOutcomeUnknownError("timeout; nothing was accepted")
        job = self.add_job(request)
        if outcome == Submit.UNKNOWN_ACCEPTED:
            raise ProviderOutcomeUnknownError("timeout after the provider accepted")
        return self._snapshot(job)

    def batch_job(self, job_id: str) -> BatchJob:
        self._record("batch_job", job_id)
        try:
            job = self.jobs[job_id]
        except KeyError:
            raise ProviderPermanentError(f"no such job {job_id}") from None
        in_progress = {BatchJobState.QUEUED, BatchJobState.PROCESSING}
        if job.state in in_progress and job.polls_until_done is not None:
            if job.polls_until_done == 0:
                job.state = BatchJobState.DONE
            else:
                job.polls_until_done -= 1
                job.state = BatchJobState.PROCESSING
        return self._snapshot(job)

    def batch_jobs_since(self, since: datetime) -> tuple[BatchJob, ...]:
        self._record("batch_jobs_since", since)
        return tuple(
            self._snapshot(job)
            for job in self.jobs.values()
            if job.listed and job.ts_received >= since
        )

    # -- delivery ------------------------------------------------------------
    def download_batch(self, job_id: str, dest_dir: Path) -> tuple[Path, ...]:
        self._record("download_batch", job_id)
        if self.download_errors:
            raise self.download_errors.popleft()
        job = self.jobs[job_id]
        files = self.job_files(job)
        if not dest_dir.is_dir():  # as the adapter: it never creates the directory
            raise FileNotFoundError(f"[Errno 2] No such file or directory: {dest_dir}")
        written = []
        for name, content in files.items():
            path = dest_dir / name
            if not path.exists():
                path.write_bytes(content)
            written.append(path)
        return tuple(written)

    @staticmethod
    def _day_file(request: TickRequest, fixture: str, day: date) -> bytes:
        if request.schema is TickSchema.DEFINITION:  # windows the projection accepts
            return definition_file_bytes(
                request.dataset, day, request.stype_in, CME_DEFINITION_RECORDS
            )
        return day_file_bytes(fixture, request.dataset, day, request.stype_in)

    def job_files(self, job: FakeJob) -> dict[str, bytes]:
        """The files the provider delivers for ``job``, by name."""
        request = job.request
        fixture = FIXTURE_FOR_SCHEMA[request.schema]
        files: dict[str, bytes] = {}
        days = (
            request.start + timedelta(days=n)
            for n in range((request.end - request.start).days)
        )
        for day in days:
            if day.weekday() == 5 or day in self.missing_days:
                continue
            name = f"glbx-mdp3-{day:%Y%m%d}.{request.schema.value}.dbn.zst"
            files[name] = self._day_file(request, fixture, day)
        for name in JOB_JSON_FILES:
            files[name] = json.dumps({"placeholder": name}).encode()
        if self.supply_manifest:
            listed = [
                {
                    "filename": name,
                    "size": len(content),
                    "hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
                }
                for name, content in sorted(files.items())
            ]
            files[MANIFEST_NAME] = json.dumps(
                {"job_id": job.job_id, "files": listed}, indent=2
            ).encode()
        return files


def request_for(
    schema: TickSchema, start: date, end: date, *, symbols: str = "ES.FUT"
) -> TickRequest:
    """A CME parent-symbol request (the adopted jobs' shape)."""
    return TickRequest(CME_DATASET, (symbols,), SType.PARENT, schema, start, end)
