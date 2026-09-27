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
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import numpy as np

from manta_trading.data.tick.constants import DatasetCondition, SType, TickSchema

# ---------------------------------------------------------------------------
# File reading (224 decodes through these)
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
# Requests and free metadata (the preflight; 223's planning)
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
