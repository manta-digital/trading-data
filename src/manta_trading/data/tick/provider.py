"""Tick-provider protocols and their request/result types (slice 220).

Everything above ``data/tick/databento/`` — the estimate core, the CLI, and
the later acquisition and ingest passes — sees only these protocols and
frozen dataclasses, never an SDK type. Every protocol is synchronous; an
async caller moves each call off the loop with ``asyncio.to_thread``
(design Technical Decision 2).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import numpy as np

from manta_trading.data.tick.constants import SType, TickSchema

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
