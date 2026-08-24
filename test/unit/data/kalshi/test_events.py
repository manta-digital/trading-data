"""Tests for the Kalshi sync structured events (slice 262, Tasks 2.1/2.2)."""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from manta_trading.data.kalshi import events as ke

_TS = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _event(**overrides: object) -> ke.SyncEvent:
    base: dict[str, object] = {
        "run_id": uuid4(),
        "timestamp": _TS,
        "event_type": ke.SyncEventType.PHASE_FINISHED,
        "phase": "markets",
        "counts": {"fetched": 3, "written": 2},
        "transitions": {"active->closed": 1},
        "duration_ms": 42,
    }
    base.update(overrides)
    return ke.SyncEvent(**base)  # type: ignore[arg-type]


class TestSyncEventType:
    def test_values_are_identifiers(self):
        assert [t.value for t in ke.SyncEventType] == [
            "run_started",
            "phase_finished",
            "item_error",
            "run_finished",
        ]
        assert all(t.value.isidentifier() for t in ke.SyncEventType)


class TestSyncEvent:
    def test_is_kalshi_typed(self):
        names = {f.name for f in fields(ke.SyncEvent)}
        assert "symbol" not in names
        assert "granularity" not in names
        assert {
            "run_id",
            "timestamp",
            "event_type",
            "phase",
            "counts",
            "transitions",
            "ticker",
            "error",
            "duration_ms",
        } <= names

    def test_to_dict_round_trips_through_json(self):
        ev = _event(ticker="KXABC-1", error="boom")
        payload = json.loads(json.dumps(ev.to_dict()))
        assert payload["run_id"] == str(ev.run_id)
        assert payload["timestamp"] == _TS.isoformat()
        assert payload["event_type"] == "phase_finished"
        assert payload["phase"] == "markets"
        assert payload["counts"] == {"fetched": 3, "written": 2}
        assert payload["transitions"] == {"active->closed": 1}
        assert payload["ticker"] == "KXABC-1"
        assert payload["error"] == "boom"
        assert payload["duration_ms"] == 42

    def test_run_level_defaults(self):
        ev = ke.SyncEvent(
            run_id=uuid4(), timestamp=_TS, event_type=ke.SyncEventType.RUN_STARTED
        )
        d = ev.to_dict()
        assert d["phase"] is None
        assert d["counts"] == {}
        assert d["transitions"] == {}
        assert d["ticker"] is None
        assert d["error"] is None

    def test_no_acquisition_import(self):
        src = Path(ke.__file__).read_text(encoding="utf-8")
        assert "data.acquisition" not in src


class TestSinks:
    def test_null_sink_is_noop(self):
        ke.NullSyncEventSink().emit(_event())

    def test_jsonl_sink_writes_one_valid_line_per_event(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = ke.JsonlSyncEventSink(path)
        events = [_event(phase=p) for p in ("series", "markets", "events")]
        for ev in events:
            sink.emit(ev)
        sink.close()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == len(events)
        assert [json.loads(line)["phase"] for line in lines] == [
            "series",
            "markets",
            "events",
        ]

    def test_jsonl_sink_appends_and_close_is_idempotent(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = ke.JsonlSyncEventSink(path)
        sink.emit(_event())
        sink.close()
        sink.close()
        sink.emit(_event())
        sink.close()
        assert len(path.read_text(encoding="utf-8").splitlines()) == 2
