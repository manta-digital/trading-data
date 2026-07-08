"""
Tests for event emission scaffold: AcquisitionEvent, NullEventSink, JsonlEventSink.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from manta_trading.data.acquisition.events import (
    AcquisitionEvent,
    AcquisitionEventType,
    JsonlEventSink,
    NullEventSink,
)
from manta_trading.data.acquisition.state import Granularity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUN_ID = UUID("aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb")
_TS = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)


def _make_event(**overrides) -> AcquisitionEvent:
    defaults = dict(
        event_type=AcquisitionEventType.CHUNK_OK,
        run_id=_RUN_ID,
        symbol="AAPL",
        granularity=Granularity.DAILY,
        provider="eodhd",
        timestamp=_TS,
        rows_written=100,
        time_range_start=_TS,
        time_range_end=_TS,
        duration_ms=250,
        error=None,
    )
    return AcquisitionEvent(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# NullEventSink
# ---------------------------------------------------------------------------


class TestNullEventSink:
    def test_accepts_event_without_error(self):
        sink = NullEventSink()
        event = _make_event()
        # Should not raise
        sink.emit(event)

    def test_accepts_multiple_events(self):
        sink = NullEventSink()
        for _ in range(5):
            sink.emit(_make_event())

    def test_accepts_event_with_none_fields(self):
        sink = NullEventSink()
        event = _make_event(
            rows_written=None,
            time_range_start=None,
            time_range_end=None,
            duration_ms=None,
            error=None,
        )
        sink.emit(event)  # must not raise


# ---------------------------------------------------------------------------
# JsonlEventSink
# ---------------------------------------------------------------------------


class TestJsonlEventSink:
    def test_writes_one_json_line_per_event(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        sink.emit(_make_event())
        sink.close()

        lines = path.read_text().splitlines()
        assert len(lines) == 1

    def test_multiple_events_append_without_overwriting(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        sink.emit(_make_event(event_type=AcquisitionEventType.RUN_STARTED))
        sink.emit(_make_event(event_type=AcquisitionEventType.CHUNK_OK))
        sink.emit(_make_event(event_type=AcquisitionEventType.RUN_FINISHED))
        sink.close()

        lines = path.read_text().splitlines()
        assert len(lines) == 3

    def test_each_line_is_valid_json(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        sink.emit(_make_event())
        sink.close()

        line = path.read_text().strip()
        parsed = json.loads(line)  # must not raise
        assert isinstance(parsed, dict)

    def test_event_type_serialized_as_string(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        sink.emit(_make_event(event_type=AcquisitionEventType.CHUNK_FAILED))
        sink.close()

        parsed = json.loads(path.read_text())
        assert parsed["event_type"] == "chunk_failed"

    def test_datetime_serialized_as_iso8601(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        ts = datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        sink.emit(_make_event(timestamp=ts))
        sink.close()

        parsed = json.loads(path.read_text())
        # ISO-8601 string round-trips back to the same datetime
        parsed_ts = datetime.fromisoformat(parsed["timestamp"])
        assert parsed_ts == ts

    def test_uuid_serialized_as_string(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        run_id = uuid4()
        sink.emit(_make_event(run_id=run_id))
        sink.close()

        parsed = json.loads(path.read_text())
        assert parsed["run_id"] == str(run_id)

    def test_granularity_serialized_as_string(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        sink.emit(_make_event(granularity=Granularity.MINUTE))
        sink.close()

        parsed = json.loads(path.read_text())
        assert parsed["granularity"] == "minute"

    def test_none_fields_serialize_as_null(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        sink.emit(_make_event(error=None, rows_written=None, duration_ms=None))
        sink.close()

        parsed = json.loads(path.read_text())
        assert parsed["error"] is None
        assert parsed["rows_written"] is None
        assert parsed["duration_ms"] is None

    def test_appends_to_existing_file(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        # Write first batch
        sink1 = JsonlEventSink(path)
        sink1.emit(_make_event(event_type=AcquisitionEventType.RUN_STARTED))
        sink1.close()

        # Write second batch to same file
        sink2 = JsonlEventSink(path)
        sink2.emit(_make_event(event_type=AcquisitionEventType.RUN_FINISHED))
        sink2.close()

        lines = path.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event_type"] == "run_started"
        assert json.loads(lines[1])["event_type"] == "run_finished"

    def test_file_created_lazily_on_first_emit(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        # File should not exist yet
        assert not path.exists()
        sink.emit(_make_event())
        assert path.exists()
        sink.close()

    def test_close_is_idempotent(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        sink.emit(_make_event())
        sink.close()
        sink.close()  # must not raise
