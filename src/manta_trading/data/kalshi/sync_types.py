"""Types shared by the catalog sync modules (slice 262).

``SyncResult`` is what one run reports (JSON-serializable through
``to_dict``); ``classify`` turns a finished or aborted run into a
``SyncOutcome`` — never an integer, the exit-code numbers live only in
``cli/commands/kalshi.py`` (review F006).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import psycopg

from manta_trading.data.kalshi.models import Event, Market, Series
from manta_trading.providers.errors import ProviderError


class SyncPhase(StrEnum):
    """Phase names carried by ``phase_finished`` / ``item_error`` events."""

    SERIES = "series"
    MARKETS = "markets"
    EVENTS = "events"
    SETTLED = "settled"
    AWAITING = "awaiting"


class SyncOutcome(StrEnum):
    """Run classification (Decision 11); the CLI maps these to exit codes."""

    OK = "ok"
    PARTIAL = "partial"
    PROVIDER_ABORT = "provider_abort"
    STORAGE_ABORT = "storage_abort"


@dataclass(frozen=True)
class ItemError:
    ticker: str
    phase: SyncPhase
    reason: str


@dataclass
class PhaseCounts:
    fetched: int = 0
    written: int = 0
    unchanged: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "written": self.written,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
        }


@dataclass
class SyncResult:
    """What one run did — JSON-serializable through :meth:`to_dict`."""

    run_id: UUID
    started_at: datetime
    phases: dict[SyncPhase, PhaseCounts] = field(
        default_factory=lambda: {p: PhaseCounts() for p in SyncPhase}
    )
    transitions: dict[tuple[str, str], int] = field(default_factory=dict)
    settled_captured: int = 0
    windows_completed: int = 0
    watermark_ts: datetime | None = None
    awaiting_entered: int = 0
    awaiting_retired: int = 0
    awaiting_checked: int = 0
    awaiting_unreachable: int = 0
    item_errors: list[ItemError] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "started_at": self.started_at.isoformat(),
            "phases": {str(p): c.to_dict() for p, c in self.phases.items()},
            "transitions": transitions_as_dict(self.transitions),
            "settled_captured": self.settled_captured,
            "windows_completed": self.windows_completed,
            "watermark_ts": self.watermark_ts.isoformat()
            if self.watermark_ts
            else None,
            "awaiting": {
                "entered": self.awaiting_entered,
                "retired": self.awaiting_retired,
                "checked": self.awaiting_checked,
                "unreachable": self.awaiting_unreachable,
            },
            "item_errors": [
                {"ticker": e.ticker, "phase": str(e.phase), "reason": e.reason}
                for e in self.item_errors
            ],
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def transitions_as_dict(transitions: dict[tuple[str, str], int]) -> dict[str, int]:
    return {f"{a}->{b}": n for (a, b), n in transitions.items()}


def classify(
    result: SyncResult, exc: ProviderError | psycopg.OperationalError | None
) -> SyncOutcome:
    """Pure classification of a finished (or aborted) run."""
    if isinstance(exc, psycopg.OperationalError):
        return SyncOutcome.STORAGE_ABORT
    if isinstance(exc, ProviderError):
        return SyncOutcome.PROVIDER_ABORT
    if exc is not None:
        raise TypeError(f"unclassified exception {type(exc).__name__}")
    return SyncOutcome.PARTIAL if result.item_errors else SyncOutcome.OK


@dataclass
class Page:
    """Rows to write in one transaction, parents first."""

    series: list[Series] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    markets: list[Market] = field(default_factory=list)


def epoch(value: datetime) -> int:
    """Unix seconds — the granularity of every ``*_ts`` query parameter."""
    return int(value.timestamp())


async def paged[T](items: AsyncIterator[T], size: int) -> AsyncIterator[list[T]]:
    """Buffer an async iterator into lists of at most ``size``."""
    buffer: list[T] = []
    async for item in items:
        buffer.append(item)
        if len(buffer) >= size:
            yield buffer
            buffer = []
    if buffer:
        yield buffer
