"""
Acquisition event scaffold: event types, event dataclass, and sink implementations.

EventSink is a Protocol so orchestrator code has no concrete sink dependency.
Two implementations are provided:
- NullEventSink: no-op, for tests and contexts that don't need event output
- JsonlEventSink: appends one JSON line per event to a file (lazy open)

This module intentionally has no database dependency. Initiative 180 may
replace JsonlEventSink with a structured store; the Protocol interface stays stable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import IO, Protocol
from uuid import UUID

from manta_trading.data.acquisition.state import Granularity


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class AcquisitionEventType(StrEnum):
    """Structured event types emitted by the orchestrator core."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    CHUNK_OK = "chunk_ok"
    CHUNK_FAILED = "chunk_failed"

    # Slice 128 — daily-daemon CA ingest, Stage B verifier, backfill.
    CA_INGEST_SPLITS = "ca_ingest_splits"
    CA_INGEST_DIVIDENDS = "ca_ingest_dividends"
    CA_INGEST_FAILED = "ca_ingest_failed"
    VERIFY_EOD = "verify_eod"
    BACKFILL_SYMBOL = "backfill_symbol"
    QUOTA_SLEEP = "quota_sleep"
    QUOTA_WINDOW_ADVANCE = "quota_window_advance"


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------


@dataclass
class AcquisitionEvent:
    """A single structured event emitted during an acquisition run.

    Optional fields are None when not applicable to the event type
    (e.g. ``rows_written`` is None for RUN_STARTED).
    """

    event_type: AcquisitionEventType
    run_id: UUID
    symbol: str
    granularity: Granularity
    provider: str
    timestamp: datetime
    rows_written: int | None = None
    time_range_start: datetime | None = None
    time_range_end: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Sink protocol and implementations
# ---------------------------------------------------------------------------


class EventSink(Protocol):
    """Protocol for event emission targets.

    Implementors must provide ``emit(event)``. Raising inside ``emit`` is
    allowed but callers should treat emission as best-effort and log rather
    than abort if sinks fail.
    """

    def emit(self, event: AcquisitionEvent) -> None: ...


class NullEventSink:
    """No-op event sink. Accepts all events and discards them silently."""

    def emit(self, event: AcquisitionEvent) -> None:  # noqa: ARG002
        pass


def _serialize_event(event: AcquisitionEvent) -> str:
    """Serialize an AcquisitionEvent to a JSON string.

    - datetimes → ISO-8601 string
    - UUIDs → string
    - StrEnums → their string value (already a str, but explicit for clarity)
    """

    def _default(obj: object) -> str:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        raise TypeError(f"Cannot serialize {type(obj)!r}")

    payload = {
        "event_type": str(event.event_type),
        "run_id": str(event.run_id),
        "symbol": event.symbol,
        "granularity": str(event.granularity),
        "provider": event.provider,
        "timestamp": event.timestamp.isoformat(),
        "rows_written": event.rows_written,
        "time_range_start": event.time_range_start.isoformat() if event.time_range_start else None,
        "time_range_end": event.time_range_end.isoformat() if event.time_range_end else None,
        "duration_ms": event.duration_ms,
        "error": event.error,
    }
    return json.dumps(payload, default=_default)


class JsonlEventSink:
    """Appends one JSON line per event to a file (lazy open, append mode).

    The file is opened on first emit and kept open until ``close()`` is called.

    Args:
        path: Destination file path. Created if it does not exist.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None

    def emit(self, event: AcquisitionEvent) -> None:
        """Serialize event to JSON and write a terminated line."""
        if self._file is None:
            self._file = self._path.open("a", encoding="utf-8")
        self._file.write(_serialize_event(event) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Flush and close the underlying file handle."""
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None
