"""Every ingest failure reason starts with its check's name (slice 225, TD8)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.ingest_checks import (
    IngestCheck,
    counts_reason,
    decode_reason,
    no_session_reason,
    outside_span_reason,
    overlap_reason,
    planning_span_reason,
    resolution_reason,
    shape_reason,
    utc_of_ns,
)

CHICAGO = ZoneInfo("America/Chicago")
OPEN = datetime(2024, 9, 2, 22, tzinfo=UTC)
CLOSE = datetime(2024, 9, 3, 21, tzinfo=UTC)
SESSION = Session("CME_EQUITY", date(2024, 9, 3), OPEN, CLOSE)
TS_NS = 1_725_400_800_000_000_000  # 2024-09-03T22:00Z

REASONS = [
    (IngestCheck.COUNTS, counts_reason(10, 10, 9)),
    (IngestCheck.RESOLUTION, resolution_reason("ES", 42, TS_NS, 3)),
    (IngestCheck.SESSION_BOUNDARY, outside_span_reason(TS_NS, OPEN, CLOSE, 2)),
    (
        IngestCheck.SESSION_BOUNDARY,
        no_session_reason(TS_NS, CHICAGO, SESSION, None, 1),
    ),
    (IngestCheck.SESSION_BOUNDARY, planning_span_reason(date(2031, 1, 2), None, None)),
    (
        IngestCheck.OVERLAP,
        overlap_reason("Key (k)=(1) already exists.", date(2024, 9, 3), [5]),
    ),
    (IngestCheck.SHAPE, shape_reason("raw_symbol")),
    (IngestCheck.DECODE, decode_reason(Path("JOB/f.dbn.zst"), OSError("gone"))),
]


@pytest.mark.parametrize(("check", "reason"), REASONS)
def test_reason_starts_with_its_check(check: IngestCheck, reason: str) -> None:
    assert reason.startswith(f"{check.value}: ")


def test_every_check_has_a_reason() -> None:
    assert {check for check, _ in REASONS} == set(IngestCheck)


def test_counts_reason_names_all_three() -> None:
    assert counts_reason(None, 5, 4) == "counts: provider None, decoded 5, stored 4"


def test_no_session_reason_names_both_zones_and_the_sessions() -> None:
    reason = no_session_reason(TS_NS, CHICAGO, SESSION, None, 1)
    assert "2024-09-03T22:00:00+00:00" in reason
    assert "2024-09-03T17:00:00-05:00 America/Chicago" in reason
    assert "session 2024-09-03 [2024-09-02T17:00:00-05:00" in reason
    assert reason.endswith("and session none")


def test_utc_of_ns() -> None:
    assert utc_of_ns(TS_NS) == datetime(2024, 9, 3, 22, tzinfo=UTC)
