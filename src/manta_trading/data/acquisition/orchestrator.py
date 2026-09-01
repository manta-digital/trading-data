"""
Orchestrator core: provider-agnostic fetch → validate → write → checkpoint loop.

The single entry point is ``run_acquisition_unit``. Callers (daemons, CLI)
supply a WorkItem, a ChunkProvider, a ChunkWriter, a state repository, an
event sink, and a run_id. The orchestrator does not retry on failure — that
is the daemon's responsibility (slices 123, 125).

Design notes:
- Async fetch, sync store: ``ChunkProvider.fetch_chunks`` is an async generator;
  ``ChunkWriter.write`` is sync and called inside ``asyncio.to_thread``.
- Checkpoint-per-chunk: state is written after each successful chunk write, so
  a crash between chunks loses at most one in-flight chunk.
- No silent failures: every error path emits an event and updates state before
  propagating or returning.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, AsyncGenerator, Protocol
from uuid import UUID

from manta_trading.data.acquisition.events import (
    AcquisitionEvent,
    AcquisitionEventType,
    EventSink,
)
from manta_trading.data.acquisition.state import (
    AcquisitionStateRepository,
    AcquisitionStateRow,
    Granularity,
    LastAttemptOutcome,
)
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


@dataclass
class WorkItem:
    """Describes what to fetch: the target and time range."""

    symbol: str
    granularity: Granularity
    provider: str
    time_range_start: datetime
    time_range_end: datetime


@dataclass
class FetchedChunk:
    """A single chunk of data returned by a provider.

    ``rows`` is provider-specific; the writer knows how to interpret it.
    """

    rows: Any
    chunk_start: datetime
    chunk_end: datetime


@dataclass
class ChunkResult:
    """Result of writing a single chunk.

    ``last_written_ts`` is the actual last timestamp written to the store.
    This may be earlier than ``chunk_end`` when the provider returned partial
    data — callers must use this value, not ``chunk_end``, as the watermark.
    """

    last_written_ts: datetime | None
    rows_written: int


class RunStatus(StrEnum):
    """In-memory result status for a single ``run_acquisition_unit`` call.

    This is a return-value enum, not a DB column. The persistent outcome
    is recorded in ``acquisition_state.last_attempt_outcome`` via
    ``LastAttemptOutcome``.
    """

    OK = "ok"
    FAILED = "failed"


@dataclass
class AcquisitionResult:
    """Summary returned by ``run_acquisition_unit``."""

    chunks_attempted: int
    chunks_written: int
    chunks_failed: int
    final_status: RunStatus
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class ChunkProvider(Protocol):
    """Yields chunks of data for a work item one at a time (async generator)."""

    def fetch_chunks(
        self, work_item: WorkItem
    ) -> AsyncGenerator[FetchedChunk, None]: ...


class ChunkWriter(Protocol):
    """Synchronous writer: persists a fetched chunk and returns the result."""

    def write(self, chunk: FetchedChunk) -> ChunkResult: ...


# ---------------------------------------------------------------------------
# Orchestrator entry point
# ---------------------------------------------------------------------------


async def run_acquisition_unit(
    work_item: WorkItem,
    provider: ChunkProvider,
    writer: ChunkWriter,
    state_repo: AcquisitionStateRepository,
    event_sink: EventSink,
    run_id: UUID,
) -> AcquisitionResult:
    """Execute one acquisition unit: fetch → write → checkpoint per chunk.

    Args:
        work_item: What to fetch (symbol, granularity, provider, time range).
        provider: Yields ``FetchedChunk`` objects via async generator.
        writer: Sync callable that persists a chunk; returns ``ChunkResult``.
        state_repo: Repository for reading/writing ``acquisition_state``.
        event_sink: Receives structured events (best-effort; emit errors are logged).
        run_id: Unique identifier for this run.

    Returns:
        ``AcquisitionResult`` summarising what happened.
    """
    start_time = time.monotonic()
    now = _utcnow()

    # Step 1: Mark attempt timestamp (slimmed shape, slice 142). The
    # outcome column stays NULL until the run finishes — daemons read
    # ``last_attempt_ts`` only for liveness checks.
    state_repo.upsert(
        AcquisitionStateRow(
            symbol=work_item.symbol,
            granularity=work_item.granularity,
            provider=work_item.provider,
            last_attempt_ts=now,
            last_attempt_outcome=None,
        )
    )

    # Step 1b: Emit RUN_STARTED
    _emit(
        event_sink,
        AcquisitionEvent(
            event_type=AcquisitionEventType.RUN_STARTED,
            run_id=run_id,
            symbol=work_item.symbol,
            granularity=work_item.granularity,
            provider=work_item.provider,
            timestamp=now,
        ),
    )

    chunks_attempted = 0
    chunks_written = 0
    last_error: str | None = None
    final_status = RunStatus.OK
    # Track the last chunk seen so fetch-failure handling can reference its range
    last_chunk: FetchedChunk | None = None

    # Step 2: Iterate chunks. Both fetch and write failures are handled here.
    # Fetch failures arise from the async generator during iteration, so the
    # entire loop is wrapped in try/except. Writer failures are caught in the
    # inner try/except. Both paths update state and emit before breaking.
    chunk_iter = provider.fetch_chunks(work_item).__aiter__()
    while True:
        chunk_start_ms = time.monotonic()
        try:
            chunk = await chunk_iter.__anext__()
        except StopAsyncIteration:
            break
        except Exception as exc:  # noqa: BLE001 — fetch failure
            last_error = str(exc)
            final_status = RunStatus.FAILED
            chunks_attempted += 1

            state_repo.upsert(
                AcquisitionStateRow(
                    symbol=work_item.symbol,
                    granularity=work_item.granularity,
                    provider=work_item.provider,
                    last_attempt_ts=_utcnow(),
                    last_attempt_outcome=LastAttemptOutcome.TRANSIENT_FAILURE,
                )
            )
            _emit(
                event_sink,
                AcquisitionEvent(
                    event_type=AcquisitionEventType.CHUNK_FAILED,
                    run_id=run_id,
                    symbol=work_item.symbol,
                    granularity=work_item.granularity,
                    provider=work_item.provider,
                    timestamp=_utcnow(),
                    time_range_start=last_chunk.chunk_start if last_chunk else None,
                    time_range_end=last_chunk.chunk_end if last_chunk else None,
                    duration_ms=_elapsed_ms(chunk_start_ms),
                    error=last_error,
                ),
            )
            break

        last_chunk = chunk
        chunks_attempted += 1

        try:
            chunk_result: ChunkResult = await asyncio.to_thread(writer.write, chunk)
        except Exception as exc:  # noqa: BLE001 — writer failure
            last_error = str(exc)
            final_status = RunStatus.FAILED

            state_repo.upsert(
                AcquisitionStateRow(
                    symbol=work_item.symbol,
                    granularity=work_item.granularity,
                    provider=work_item.provider,
                    last_attempt_ts=_utcnow(),
                    last_attempt_outcome=LastAttemptOutcome.TRANSIENT_FAILURE,
                )
            )
            _emit(
                event_sink,
                AcquisitionEvent(
                    event_type=AcquisitionEventType.CHUNK_FAILED,
                    run_id=run_id,
                    symbol=work_item.symbol,
                    granularity=work_item.granularity,
                    provider=work_item.provider,
                    timestamp=_utcnow(),
                    time_range_start=chunk.chunk_start,
                    time_range_end=chunk.chunk_end,
                    duration_ms=_elapsed_ms(chunk_start_ms),
                    error=last_error,
                ),
            )
            break  # do not attempt further chunks

        # Success path: checkpoint state immediately. The slimmed schema no
        # longer carries last_success_ts; the watermark is now derivable
        # from MAX(time) on the bar tables (slice 144's responsibility).
        chunks_written += 1
        outcome = (
            LastAttemptOutcome.SUCCESS
            if chunk_result.rows_written > 0
            else LastAttemptOutcome.EMPTY
        )
        state_repo.upsert(
            AcquisitionStateRow(
                symbol=work_item.symbol,
                granularity=work_item.granularity,
                provider=work_item.provider,
                last_attempt_ts=_utcnow(),
                last_attempt_outcome=outcome,
            )
        )
        _emit(
            event_sink,
            AcquisitionEvent(
                event_type=AcquisitionEventType.CHUNK_OK,
                run_id=run_id,
                symbol=work_item.symbol,
                granularity=work_item.granularity,
                provider=work_item.provider,
                timestamp=_utcnow(),
                rows_written=chunk_result.rows_written,
                time_range_start=chunk.chunk_start,
                time_range_end=chunk.chunk_end,
                duration_ms=_elapsed_ms(chunk_start_ms),
            ),
        )

    # Step 3: Emit RUN_FINISHED
    total_ms = _elapsed_ms(start_time)
    _emit(
        event_sink,
        AcquisitionEvent(
            event_type=AcquisitionEventType.RUN_FINISHED,
            run_id=run_id,
            symbol=work_item.symbol,
            granularity=work_item.granularity,
            provider=work_item.provider,
            timestamp=_utcnow(),
            duration_ms=total_ms,
            error=last_error,
        ),
    )

    # Step 4: Return result
    return AcquisitionResult(
        chunks_attempted=chunks_attempted,
        chunks_written=chunks_written,
        chunks_failed=chunks_attempted - chunks_written,
        final_status=final_status,
        last_error=last_error,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _emit(sink: EventSink, event: AcquisitionEvent) -> None:
    """Emit an event; log and continue on sink error (best-effort)."""
    try:
        sink.emit(event)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Event sink emit failed (continuing): %s", exc)
