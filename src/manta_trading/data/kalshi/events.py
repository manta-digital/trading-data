"""Structured events for the Kalshi catalog sync (slice 262, Decision 14).

Mirrors the *shape* of ``data/acquisition/events.py`` — type enum, frozen
event dataclass, sink Protocol, Null and Jsonl sinks — but is Kalshi-typed:
no ``symbol``/``granularity`` fields, and nothing imported from the
acquisition package. Per-item events exist only for errors; everything else
is aggregated per phase so a sink is not flooded by ~74k settlements a day.

Sinks do not swallow their own failures. Emission is best-effort at the
*caller* (``sync.py`` wraps ``emit``: log at ERROR, never abort the run),
and the caller runs ``emit`` in a worker thread, so a sink may block on I/O
(``JsonlSyncEventSink`` does) without stalling the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import IO, Any, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class SyncEventType(StrEnum):
    """Event types emitted by the catalog sync and, since slice 263, the pass.

    ``PASS_STARTED`` / ``PASS_FINISHED`` bracket a
    :class:`~manta_trading.data.kalshi.collection_pass.CollectionPass` and
    carry ``phase=None`` (design 263, Decision 3); the events between them
    are the phases' own, sharing the pass's ``run_id``.
    """

    RUN_STARTED = "run_started"
    PHASE_FINISHED = "phase_finished"
    ITEM_ERROR = "item_error"
    RUN_FINISHED = "run_finished"
    PASS_STARTED = "pass_started"
    PASS_FINISHED = "pass_finished"


@dataclass(frozen=True)
class SyncEvent:
    """One structured event of a sync run.

    ``phase`` names the sync phase (``series``, ``markets``, ...) for
    ``phase_finished`` / ``item_error``; ``None`` for run-level events.
    ``counts`` and ``transitions`` are aggregates (``transitions`` keys are
    ``"from->to"`` strings so the dict is JSON-serializable as-is).
    """

    run_id: UUID
    timestamp: datetime
    event_type: SyncEventType
    phase: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    transitions: dict[str, int] = field(default_factory=dict)
    ticker: str | None = None
    error: str | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable mapping (``json.dumps`` needs no ``default``)."""
        return {
            "run_id": str(self.run_id),
            "timestamp": self.timestamp.isoformat(),
            "event_type": str(self.event_type),
            "phase": self.phase,
            "counts": dict(self.counts),
            "transitions": dict(self.transitions),
            "ticker": self.ticker,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class SyncEventSink(Protocol):
    """Emission target. Raising inside ``emit`` is allowed; callers treat it
    as best-effort."""

    def emit(self, event: SyncEvent) -> None: ...


class NullSyncEventSink:
    """Discards every event."""

    def emit(self, event: SyncEvent) -> None:  # noqa: ARG002
        pass


class JsonlSyncEventSink:
    """Appends one JSON object per line to ``path`` (lazy open, append mode).

    The file opens on the first ``emit`` and stays open until :meth:`close`.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None

    def emit(self, event: SyncEvent) -> None:
        if self._file is None:
            self._file = self._path.open("a", encoding="utf-8")
        self._file.write(json.dumps(event.to_dict()) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Flush and close the file handle (idempotent)."""
        if self._file is not None:
            self._file.close()
            self._file = None


async def emit_in_thread(sink: SyncEventSink, event: SyncEvent) -> None:
    """Best-effort emission off the event loop, shared by the sync cores.

    The sink call runs in a worker thread (code review 262 F001): a
    ``JsonlSyncEventSink`` does a synchronous open/write/flush, which the
    project's async rule keeps off the loop. A sink failure is logged and
    never aborts the run. Each core is a single sequential writer, so one
    sink call at a time reaches the thread.
    """
    try:
        await asyncio.to_thread(sink.emit, event)
    except Exception:
        logger.exception("event sink failed on %s", event.event_type)
