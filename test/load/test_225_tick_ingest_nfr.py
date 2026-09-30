"""Load test for slice 225's ingest pass on the largest real adopted day.

Ingest is on the concurrency path (a worker thread per unit, its own
connection, a ``COPY`` fed batch by batch), so the Python rules require a load
test with a latency or resource bound. The realistic configuration is the
largest day the archive holds: 2024-09-03 ``trades`` (511,965 records), read
from ``/data/tick-archive`` and ingested through the real pass (``run_ingest``
via ``IngestPhase``) into a fixture database. If the file is absent the test
**fails** naming the path; it never skips.

Bounds, from the architecture's two targets (LLD 225 TD2):

* **Unit time ≤ 120 s** — 1/720 of the 24 h of market time the unit covers:
  "a day's sessions ingest far faster than a day of market time". A month of
  such days is then well inside an operator's working session.
* **Event-loop gap ≤ 250 ms** — a heartbeat task measures the longest gap the
  loop went without running it. The worker runs in a thread, so a long gap
  means blocking work ran on the loop.

Gate (CI runs no test job; see slice 907 — this gate is the load tier's gate,
as for every other load test):

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_225_tick_ingest_nfr.py

Requires ``MT_TIMESCALE_TEST_URL``. This tier never reads the production DB
URL; ``test_load_tier_never_references_prod_db_url`` enforces that.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date
from pathlib import Path

import pytest
from tick_support.ingest import ingest_inputs, run_ingest_phase, trade_count
from tick_support.tier_units import seed_tier_unit

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.pass_contract import TickOutcome

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(600),
]

ARCHIVE = Path("/data/tick-archive")
DAY = date(2024, 9, 3)
TRADES = ARCHIVE / "GLBX-20240930-USM7UXXJBA" / "glbx-mdp3-20240903.trades.dbn.zst"
DEFINITIONS = (
    ARCHIVE / "GLBX-20260930-DLDYL5DM8Q" / "glbx-mdp3-20240903.definition.dbn.zst"
)
RECORDS = 511_965

#: Budgets in seconds, from the targets above (not from a measurement).
#: Measured 2026-09-30 on manta9000 against the test cluster, 3 runs: unit
#: 1.19-1.23 s (decode 0.04 s, write 1.16-1.19 s), longest loop gap 28-38 ms.
#: The first run found a 336 ms gap: a 2-D ``np.unique`` in the ledger held the
#: GIL on the worker thread; the packed-key sort that replaced it takes 30 ms.
UNIT_BUDGET_S = 120.0
MAX_LOOP_GAP_S = 0.25
HEARTBEAT_S = 0.005


class Heartbeat:
    """The longest gap between two ticks of a task that asks to run often."""

    def __init__(self) -> None:
        self.max_gap = 0.0
        self._task: asyncio.Task[None] | None = None

    async def _beat(self) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            now = time.monotonic()
            self.max_gap = max(self.max_gap, now - last - HEARTBEAT_S)
            last = now

    def __enter__(self) -> Heartbeat:
        self._task = asyncio.create_task(self._beat())
        return self

    def __exit__(self, *_: object) -> None:
        assert self._task is not None
        self._task.cancel()


async def test_the_largest_real_day_ingests_in_budget(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    for path in (TRADES, DEFINITIONS):
        assert path.is_file(), f"archived day missing: {path}"
    seeded = await seed_tier_unit(
        migrated_tick_db,
        tmp_path,
        TickSchema.TRADES,
        DAY,
        job_id="GLBX-20240930-USM7UXXJBA",
        tier_file=TRADES,
        definition_file=DEFINITIONS,
    )
    assert seeded.record_count == RECORDS
    inputs = ingest_inputs(migrated_tick_db, session_migrated_db)
    started = time.monotonic()
    with Heartbeat() as beat:
        report = await run_ingest_phase(migrated_tick_db, tmp_path, inputs)
    elapsed = time.monotonic() - started
    (unit,) = report.summary["units"]
    print(
        f"\n{RECORDS} records: run {elapsed:.2f}s (unit {unit['duration_seconds']}s:"
        f" decode {unit['decode_seconds']}s, write {unit['write_seconds']}s);"
        f" longest loop gap {beat.max_gap:.3f}s"
    )
    assert report.outcome is TickOutcome.OK, report.error
    assert trade_count(migrated_tick_db, seeded.unit_id) == RECORDS
    assert unit["duration_seconds"] < UNIT_BUDGET_S
    assert beat.max_gap < MAX_LOOP_GAP_S
