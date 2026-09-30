"""Tick-provider protocols and their request/result types (slice 220).

Everything above ``data/tick/databento/`` — the estimate core, the CLI, and
the later acquisition and ingest passes — sees only these protocols and
frozen dataclasses, never an SDK type. Every protocol is synchronous; an
async caller moves each call off the loop with ``asyncio.to_thread``
(design Technical Decision 2).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import numpy as np

from manta_trading.data.tick.constants import (
    BatchJobState,
    DatasetCondition,
    SType,
    TickSchema,
)

# ---------------------------------------------------------------------------
# File reading (225 decodes through these)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolInterval:
    """A header mapping interval: ``instrument_id`` over ``[start_date, end_date)``."""

    start_date: date
    end_date: date
    instrument_id: int


@dataclass(frozen=True)
class RecordBatch:
    """One bounded batch of decoded records.

    ``records`` is a NumPy structured array in the provider's own field layout
    (fixed-point prices, nanosecond timestamps); ``count == len(records)``.
    """

    schema: TickSchema
    records: np.ndarray
    count: int


class TickFileDecodeError(Exception):
    """A tick file's bytes cannot be decoded (corrupt or truncated framing).

    The reader raises this in place of the SDK's own errors, so callers need no
    SDK import (220 TD 4). A missing or unreadable file stays an ``OSError``.
    """


class ITickFile(Protocol):
    """One opened tick archive file: its header, then its records in batches.

    Belongs to one thread: it owns its reader for the life of an iteration and
    must not be shared. Open one per worker from a shared ``ITickFileReader``.
    """

    dataset: str
    schema: TickSchema
    stype_in: SType
    start: datetime
    end: datetime
    #: Input symbol → the instrument-id intervals it resolved to.
    mappings: Mapping[str, tuple[SymbolInterval, ...]]
    partial: tuple[str, ...]
    not_found: tuple[str, ...]

    def iter_batches(self) -> Iterator[RecordBatch]:
        """Yield the file's records, each batch ≤ ``TICK_DECODE_BATCH_BYTES``."""
        ...


class ITickFileReader(Protocol):
    """Opens tick archive files. Stateless — no client, no key — so one
    instance may be shared by every thread."""

    def open_file(self, path: Path) -> ITickFile: ...


# ---------------------------------------------------------------------------
# Requests and free metadata (the preflight; 224's planning)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickRequest:
    """One provider request: instruments × schema × ``[start, end)`` in UTC days.

    ``end`` is **exclusive**, as the provider's cost, size, record-count, and
    submit endpoints treat it (design Technical Decision 8). Immutable;
    ``with_schema`` gives the same instruments and range at another schema.
    """

    dataset: str
    symbols: tuple[str, ...]
    stype_in: SType
    schema: TickSchema
    start: date
    end: date

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("TickRequest needs at least one symbol")
        if self.end <= self.start:
            raise ValueError(
                f"TickRequest end {self.end} must be after start {self.start} "
                "(end is exclusive)"
            )

    def with_schema(self, schema: TickSchema) -> TickRequest:
        return replace(self, schema=schema)

    @property
    def start_utc(self) -> datetime:
        """``start`` as the aware UTC instant that opens the range."""
        return datetime.combine(self.start, time(), UTC)

    @property
    def end_utc(self) -> datetime:
        """``end`` as the aware UTC instant that closes the range (exclusive)."""
        return datetime.combine(self.end, time(), UTC)


@dataclass(frozen=True)
class DatasetRange:
    """The dataset's available range for this account; ``end`` is exclusive."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class DayCondition:
    """The provider's condition for one UTC day of a dataset."""

    day: date
    condition: DatasetCondition
    last_modified: date | None


@dataclass(frozen=True)
class SymbolResolution:
    """Input symbols resolved to instrument-id intervals, plus the leftovers."""

    mappings: Mapping[str, tuple[SymbolInterval, ...]]
    partial: tuple[str, ...]
    not_found: tuple[str, ...]


class ITickMetadataProvider(Protocol):
    """Free metadata calls only — nothing here can spend.

    Not thread-safe: one instance per concurrent caller. An async caller
    wraps each call in ``asyncio.to_thread`` and never shares an instance
    between tasks.
    """

    def dataset_range(self, dataset: str) -> DatasetRange: ...

    def dataset_condition(
        self, dataset: str, start: date, end: date
    ) -> tuple[DayCondition, ...]:
        """One condition per day of ``[start, end)`` (``end`` exclusive)."""
        ...

    def record_count(self, request: TickRequest) -> int: ...

    def billable_size(self, request: TickRequest) -> int:
        """Billable uncompressed size in bytes."""
        ...

    def cost(self, request: TickRequest) -> Decimal:
        """Cost in USD."""
        ...

    def resolve_symbols(self, request: TickRequest) -> SymbolResolution: ...


# ---------------------------------------------------------------------------
# Acquisition (224 only; two methods spend money)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchJob:
    """A batch job as the provider reports it; 222's manifest row source.

    ``ts_expiration`` is the only source of a unit's download deadline. The
    size and cost fields are ``None`` until the provider has processed the job.
    """

    job_id: str
    request: TickRequest
    state: BatchJobState
    ts_received: datetime
    ts_expiration: datetime | None
    record_count: int | None
    billed_size: int | None
    actual_size: int | None
    package_size: int | None
    cost_usd: Decimal | None


class ITickAcquisitionProvider(Protocol):
    """Buying and fetching tick files. ``fetch_range`` and ``submit_batch`` are
    **PAID**: a failure that does not prove the provider refused raises
    ``ProviderOutcomeUnknownError`` — reconcile before resubmitting.

    Not thread-safe: one instance per concurrent caller (one per worker).
    """

    def fetch_range(self, request: TickRequest, dest: Path) -> Path:
        """PAID. Stream the request to ``dest``; ``dest`` exists only if complete."""
        ...

    def submit_batch(self, request: TickRequest) -> BatchJob:
        """PAID. Submit a batch job and read it back."""
        ...

    def batch_job(self, job_id: str) -> BatchJob: ...

    def batch_jobs_since(self, since: datetime) -> tuple[BatchJob, ...]:
        """Every job submitted since ``since``, for reconciling an unknown submit."""
        ...

    def download_batch(self, job_id: str, dest_dir: Path) -> tuple[Path, ...]:
        """Download the job's files; a final name is size- and SHA-256-verified."""
        ...
