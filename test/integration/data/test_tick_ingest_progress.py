"""The ingest pass's per-batch progress hook (slice 234, Data Flow step 2).

Counts are rows ``COPY``'d inside a unit's open transaction, reported from
worker threads. They are a liveness signal: a unit that later fails its checks
has still reported what it copied.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from tick_support.ingest import ingest_inputs, run_ingest_phase, unit_state
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit
from tick_support.universe import use_untiered

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.ingest_pass import IngestInputs
from manta_trading.data.tick.pass_contract import TickOutcome


@pytest.fixture(autouse=True)
def untiered(monkeypatch: pytest.MonkeyPatch) -> None:
    use_untiered(monkeypatch)


class _Counter:
    """A thread-safe hook that keeps every batch count it is handed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.batches: list[int] = []
        self.threads: set[int] = set()

    def __call__(self, rows: int) -> None:
        with self._lock:
            self.batches.append(rows)
            self.threads.add(threading.get_ident())


def test_the_hook_defaults_to_none() -> None:
    assert IngestInputs.__dataclass_fields__["on_batch_rows"].default is None


async def test_reported_batches_sum_to_the_rows_loaded(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    trades = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )
    tbbo = await seed_tier_unit(url, tmp_path, TickSchema.TBBO, TBBO_DAY, job_id="B")
    counter = _Counter()
    inputs = ingest_inputs(url, session_migrated_db, on_batch_rows=counter)
    report = await run_ingest_phase(url, tmp_path, inputs)
    assert report.outcome is TickOutcome.OK
    assert sum(counter.batches) == trades.record_count + tbbo.record_count
    assert sum(counter.batches) == report.summary["records"]
    assert all(rows > 0 for rows in counter.batches)
    assert threading.get_ident() not in counter.threads  # called on workers


async def test_a_unit_failing_its_checks_has_still_reported_its_copied_rows(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    """What the code does: every batch is reported as it is copied, and the
    counts check runs after the COPY, so the rolled-back unit's rows were
    reported. This is why the count is liveness, never a committed total."""
    url = migrated_tick_db
    seeded = await seed_tier_unit(
        url,
        tmp_path,
        TickSchema.TRADES,
        TRADES_DAY,
        job_id="T",
        provider_record_count=1,
    )
    counter = _Counter()
    inputs = ingest_inputs(url, session_migrated_db, on_batch_rows=counter)
    report = await run_ingest_phase(url, tmp_path, inputs)
    assert report.outcome is TickOutcome.PARTIAL
    assert unit_state(url, seeded.unit_id)[0] == "verified"
    assert sum(counter.batches) == seeded.record_count
