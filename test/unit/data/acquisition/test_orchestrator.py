"""
Tests for the orchestrator core (run_acquisition_unit).

Uses fakes only — no database, no real providers. Tests verify:
- Happy path: multi-chunk fetch, all written, watermark correct
- Fetch failure mid-stream: chunks before failure are committed
- Writer failure mid-stream: same checkpoint guarantee
- Partial response: watermark reflects actual last_written_ts, not chunk_end
- RUN_STARTED / RUN_FINISHED always emitted (even on failure)
- Subsequent run after failure preserves watermark from the failed run

The resume property test (fetch failure on chunk 2) is the critical test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from manta_trading.data.acquisition.events import (
    AcquisitionEvent,
    AcquisitionEventType,
    NullEventSink,
)
from manta_trading.data.acquisition.orchestrator import (
    AcquisitionResult,
    ChunkResult,
    FetchedChunk,
    RunStatus,
    WorkItem,
    run_acquisition_unit,
)
from manta_trading.data.acquisition.state import (
    AcquisitionStateRepository,
    AcquisitionStateRow,
    Granularity,
    LastAttemptOutcome,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, 0, 0, 0, tzinfo=timezone.utc)


def _make_work_item(symbol: str = "AAPL") -> WorkItem:
    return WorkItem(
        symbol=symbol,
        granularity=Granularity.DAILY,
        provider="test_provider",
        time_range_start=_dt(1),
        time_range_end=_dt(31),
    )


def _make_chunk(day: int, rows: int = 10) -> FetchedChunk:
    return FetchedChunk(
        rows=[f"row_{i}" for i in range(rows)],
        chunk_start=_dt(day),
        chunk_end=_dt(day + 1),
    )


class FakeProvider:
    """Yields a fixed list of chunks. Raises on the configured failure index."""

    def __init__(self, chunks: list[FetchedChunk], fail_on_index: int | None = None):
        self._chunks = chunks
        self._fail_on_index = fail_on_index

    async def fetch_chunks(self, work_item: WorkItem) -> AsyncIterator[FetchedChunk]:
        for i, chunk in enumerate(self._chunks):
            if self._fail_on_index is not None and i == self._fail_on_index:
                raise RuntimeError(f"Simulated fetch failure at chunk {i}")
            yield chunk


class FakeWriter:
    """Writes chunks successfully unless configured to fail on a given index."""

    def __init__(self, fail_on_index: int | None = None, partial_ts: datetime | None = None):
        self._fail_on_index = fail_on_index
        self._partial_ts = partial_ts
        self._call_count = 0

    def write(self, chunk: FetchedChunk) -> ChunkResult:
        idx = self._call_count
        self._call_count += 1
        if self._fail_on_index is not None and idx == self._fail_on_index:
            raise RuntimeError(f"Simulated write failure at chunk {idx}")
        last_ts = self._partial_ts if self._partial_ts else chunk.chunk_end
        return ChunkResult(last_written_ts=last_ts, rows_written=len(chunk.rows))


class FakeEventSink:
    """Captures all emitted events for assertion."""

    def __init__(self):
        self.events: list[AcquisitionEvent] = []

    def emit(self, event: AcquisitionEvent) -> None:
        self.events.append(event)

    def event_types(self) -> list[AcquisitionEventType]:
        return [e.event_type for e in self.events]


class InMemoryStateRepository:
    """In-memory implementation of AcquisitionStateRepository interface for testing."""

    def __init__(self):
        self._store: dict[tuple, AcquisitionStateRow] = {}

    def upsert(self, row: AcquisitionStateRow) -> None:
        key = (row.symbol, row.granularity, row.provider)
        self._store[key] = row

    def get(
        self,
        symbol: str,
        granularity: Granularity,
        provider: str,
    ) -> AcquisitionStateRow | None:
        return self._store.get((symbol, granularity, provider))

    def list(
        self,
        *,
        symbol: str | None = None,
        granularity: Granularity | None = None,
        provider: str | None = None,
    ) -> list[AcquisitionStateRow]:
        rows = list(self._store.values())
        if symbol is not None:
            rows = [r for r in rows if r.symbol == symbol]
        if granularity is not None:
            rows = [r for r in rows if r.granularity == granularity]
        if provider is not None:
            rows = [r for r in rows if r.provider == provider]
        return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


WORK = _make_work_item()
RUN_ID = uuid4()


class TestOrchestratorHappyPath:
    """Provider yields 3 chunks, all write successfully."""

    @pytest.mark.asyncio
    async def test_final_status_ok(self):
        provider = FakeProvider([_make_chunk(1), _make_chunk(5), _make_chunk(10)])
        writer = FakeWriter()
        state = InMemoryStateRepository()
        events = FakeEventSink()

        result = await run_acquisition_unit(WORK, provider, writer, state, events, RUN_ID)

        assert result.final_status == RunStatus.OK
        assert result.chunks_attempted == 3
        assert result.chunks_written == 3
        assert result.chunks_failed == 0

    @pytest.mark.asyncio
    async def test_last_attempt_outcome_is_success(self):
        """Slice 142: ``last_success_ts`` no longer carried; outcome enum
        records success instead. The bar-table watermark is the source of truth.
        """
        last_chunk = _make_chunk(10)
        provider = FakeProvider([_make_chunk(1), _make_chunk(5), last_chunk])
        writer = FakeWriter()
        state = InMemoryStateRepository()

        await run_acquisition_unit(
            WORK, provider, writer, state, NullEventSink(), RUN_ID
        )

        row = state.get(WORK.symbol, WORK.granularity, WORK.provider)
        assert row is not None
        assert row.last_attempt_outcome == LastAttemptOutcome.SUCCESS
        assert row.last_attempt_ts is not None

    @pytest.mark.asyncio
    async def test_chunk_ok_events_emitted_for_each_chunk(self):
        provider = FakeProvider([_make_chunk(1), _make_chunk(5), _make_chunk(10)])
        events = FakeEventSink()

        await run_acquisition_unit(WORK, provider, FakeWriter(), InMemoryStateRepository(), events, RUN_ID)

        chunk_ok_count = sum(
            1 for e in events.events if e.event_type == AcquisitionEventType.CHUNK_OK
        )
        assert chunk_ok_count == 3

    @pytest.mark.asyncio
    async def test_run_started_and_run_finished_emitted(self):
        provider = FakeProvider([_make_chunk(1)])
        events = FakeEventSink()

        await run_acquisition_unit(WORK, provider, FakeWriter(), InMemoryStateRepository(), events, RUN_ID)

        types = events.event_types()
        assert AcquisitionEventType.RUN_STARTED in types
        assert AcquisitionEventType.RUN_FINISHED in types

    @pytest.mark.asyncio
    async def test_no_retry_count_field(self):
        """Slice 142: retry tracking moved to ``data_gaps.attempt_count``."""
        provider = FakeProvider([_make_chunk(1), _make_chunk(5)])
        state = InMemoryStateRepository()

        await run_acquisition_unit(
            WORK, provider, FakeWriter(), state, NullEventSink(), RUN_ID
        )

        row = state.get(WORK.symbol, WORK.granularity, WORK.provider)
        assert row is not None
        assert not hasattr(row, "retry_count")


class TestOrchestratorFetchFailure:
    """Fetch raises on chunk 2 (index 1): chunk 0 committed, state from chunk 0 preserved."""

    @pytest.mark.asyncio
    async def test_final_status_failed(self):
        provider = FakeProvider([_make_chunk(1), _make_chunk(5), _make_chunk(10)], fail_on_index=1)
        result = await run_acquisition_unit(
            WORK, provider, FakeWriter(), InMemoryStateRepository(), NullEventSink(), RUN_ID
        )
        assert result.final_status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_outcome_is_transient_failure_after_fetch_failure(self):
        """Slice 142: state row records the outcome of the last attempt;
        the bar-table watermark is the source of truth for resume.
        """
        chunk0 = _make_chunk(1)  # succeeds
        chunk1 = _make_chunk(5)  # fetch will raise
        provider = FakeProvider([chunk0, chunk1], fail_on_index=1)
        state = InMemoryStateRepository()

        await run_acquisition_unit(
            WORK, provider, FakeWriter(), state, NullEventSink(), RUN_ID
        )

        row = state.get(WORK.symbol, WORK.granularity, WORK.provider)
        assert row is not None
        assert row.last_attempt_outcome == LastAttemptOutcome.TRANSIENT_FAILURE

    @pytest.mark.asyncio
    async def test_no_retry_count_field_on_failure(self):
        """Slice 142: retry tracking moved to ``data_gaps.attempt_count``."""
        provider = FakeProvider(
            [_make_chunk(1), _make_chunk(5)], fail_on_index=1
        )
        state = InMemoryStateRepository()

        await run_acquisition_unit(
            WORK, provider, FakeWriter(), state, NullEventSink(), RUN_ID
        )

        row = state.get(WORK.symbol, WORK.granularity, WORK.provider)
        assert row is not None
        assert not hasattr(row, "retry_count")

    @pytest.mark.asyncio
    async def test_chunk_failed_event_emitted(self):
        provider = FakeProvider([_make_chunk(1), _make_chunk(5)], fail_on_index=1)
        events = FakeEventSink()

        await run_acquisition_unit(WORK, provider, FakeWriter(), InMemoryStateRepository(), events, RUN_ID)

        assert AcquisitionEventType.CHUNK_FAILED in events.event_types()

    @pytest.mark.asyncio
    async def test_no_further_chunks_attempted_after_failure(self):
        chunks = [_make_chunk(i) for i in range(1, 6)]  # 5 chunks
        provider = FakeProvider(chunks, fail_on_index=1)
        writer = FakeWriter()

        result = await run_acquisition_unit(
            WORK, provider, writer, InMemoryStateRepository(), NullEventSink(), RUN_ID
        )

        # chunk 0 written, chunk 1 failed → 1 written, 1 failed, rest never attempted
        assert result.chunks_written == 1
        assert writer._call_count == 1  # writer only called for chunk 0


class TestOrchestratorWriterFailure:
    """Writer raises on chunk 2 (index 1): same checkpoint guarantee as fetch failure."""

    @pytest.mark.asyncio
    async def test_final_status_failed(self):
        chunks = [_make_chunk(1), _make_chunk(5), _make_chunk(10)]
        provider = FakeProvider(chunks)
        writer = FakeWriter(fail_on_index=1)

        result = await run_acquisition_unit(
            WORK, provider, writer, InMemoryStateRepository(), NullEventSink(), RUN_ID
        )
        assert result.final_status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_outcome_is_transient_failure_after_writer_failure(self):
        """Slice 142: outcome enum records the writer-failure path; the
        bar-table watermark is the resume source of truth.
        """
        chunk0 = _make_chunk(1)
        chunk1 = _make_chunk(5)
        provider = FakeProvider([chunk0, chunk1])
        writer = FakeWriter(fail_on_index=1)
        state = InMemoryStateRepository()

        await run_acquisition_unit(
            WORK, provider, writer, state, NullEventSink(), RUN_ID
        )

        row = state.get(WORK.symbol, WORK.granularity, WORK.provider)
        assert row is not None
        assert row.last_attempt_outcome == LastAttemptOutcome.TRANSIENT_FAILURE

    @pytest.mark.asyncio
    async def test_chunk_failed_event_emitted(self):
        provider = FakeProvider([_make_chunk(1), _make_chunk(5)])
        writer = FakeWriter(fail_on_index=1)
        events = FakeEventSink()

        await run_acquisition_unit(WORK, provider, writer, InMemoryStateRepository(), events, RUN_ID)

        assert AcquisitionEventType.CHUNK_FAILED in events.event_types()


class TestOrchestratorPartialResponse:
    """last_written_ts < chunk_end: watermark reflects actual written ts."""

    @pytest.mark.asyncio
    async def test_partial_response_marks_outcome_success_when_rows_written(self):
        """Slice 142: partial-response watermark goes to the bar table; the
        state outcome is SUCCESS as long as some rows were written.
        """
        chunk = _make_chunk(1)
        partial_ts = _dt(1) + timedelta(hours=12)  # before chunk_end
        writer = FakeWriter(partial_ts=partial_ts)
        state = InMemoryStateRepository()

        await run_acquisition_unit(
            WORK, FakeProvider([chunk]), writer, state, NullEventSink(), RUN_ID
        )

        row = state.get(WORK.symbol, WORK.granularity, WORK.provider)
        assert row is not None
        assert row.last_attempt_outcome == LastAttemptOutcome.SUCCESS


class TestOrchestratorAlwaysEmitsBookends:
    """RUN_STARTED and RUN_FINISHED emitted even when all chunks fail."""

    @pytest.mark.asyncio
    async def test_run_started_emitted_on_immediate_failure(self):
        provider = FakeProvider([_make_chunk(1)], fail_on_index=0)
        events = FakeEventSink()

        await run_acquisition_unit(WORK, provider, FakeWriter(), InMemoryStateRepository(), events, RUN_ID)

        assert events.events[0].event_type == AcquisitionEventType.RUN_STARTED

    @pytest.mark.asyncio
    async def test_run_finished_emitted_on_immediate_failure(self):
        provider = FakeProvider([_make_chunk(1)], fail_on_index=0)
        events = FakeEventSink()

        await run_acquisition_unit(WORK, provider, FakeWriter(), InMemoryStateRepository(), events, RUN_ID)

        assert events.events[-1].event_type == AcquisitionEventType.RUN_FINISHED

    @pytest.mark.asyncio
    async def test_run_finished_emitted_with_error_on_failure(self):
        provider = FakeProvider([_make_chunk(1)], fail_on_index=0)
        events = FakeEventSink()

        await run_acquisition_unit(WORK, provider, FakeWriter(), InMemoryStateRepository(), events, RUN_ID)

        finished = next(e for e in events.events if e.event_type == AcquisitionEventType.RUN_FINISHED)
        assert finished.error is not None


class TestOrchestratorOutcomeAcrossRuns:
    """Slice 142: state row records the outcome of the most recent run.

    The watermark is no longer in ``acquisition_state`` (it lives on the bar
    tables). What persists across runs is the outcome enum: a failed run
    leaves TRANSIENT_FAILURE; the next successful run flips it to SUCCESS.
    """

    @pytest.mark.asyncio
    async def test_subsequent_success_flips_outcome(self):
        # First run: chunk 0 succeeds, chunk 1 fails → TRANSIENT_FAILURE
        chunk0 = _make_chunk(1)
        chunk1 = _make_chunk(5)
        provider1 = FakeProvider([chunk0, chunk1], fail_on_index=1)
        state = InMemoryStateRepository()
        run1_id = uuid4()

        await run_acquisition_unit(
            WORK, provider1, FakeWriter(), state, NullEventSink(), run1_id
        )

        row1 = state.get(WORK.symbol, WORK.granularity, WORK.provider)
        assert row1 is not None
        assert row1.last_attempt_outcome == LastAttemptOutcome.TRANSIENT_FAILURE

        # Second run: succeeds → SUCCESS
        chunk2 = _make_chunk(5)
        provider2 = FakeProvider([chunk2])
        run2_id = uuid4()

        result2 = await run_acquisition_unit(
            WORK, provider2, FakeWriter(), state, NullEventSink(), run2_id
        )

        row2 = state.get(WORK.symbol, WORK.granularity, WORK.provider)
        assert row2 is not None
        assert row2.last_attempt_outcome == LastAttemptOutcome.SUCCESS
        assert result2.final_status == RunStatus.OK
