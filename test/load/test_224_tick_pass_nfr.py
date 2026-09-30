"""Load test for slice 224's acquisition pass at the whole-history scale.

The pass is on the network and concurrency path (provider calls, polling,
downloads, a thread per blocking call), so the Python rules require a load
test with a latency or resource bound. The realistic configuration is the
target the backfill is meant to reach: every ES session day since 2020 owned,
so the first pass plans, buys, downloads, verifies and projects a definition
request for every month of that history, and the next pass finds nothing to buy.

Two properties are asserted:

* **Wall time.** The first pass and the steady-state second pass each finish
  inside a budget with margin. Provider latency is excluded (the fake answers
  instantly and the clock is fake), so this bounds *our* cost: planning,
  manifest writes, hashing, DBN decode and projection.
* **Event-loop responsiveness.** A heartbeat task measures the longest gap the
  loop went without running it. A long gap means blocking work ran on the
  loop instead of in a thread, which stalls every other coroutine.

**Scale honesty.** The fake provider's definition files are re-headed
fixtures with a handful of instruments per day; a real ES.FUT parent day
holds tens (51 rows over 4 days in the live walkthrough). Row volume is
therefore under-represented; request, unit and file counts are real.

Gate (CI runs no test job; see slice 907):

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_224_tick_pass_nfr.py

Requires ``MT_TIMESCALE_TEST_URL``. This tier never reads the production DB
URL; ``test_load_tier_never_references_prod_db_url`` enforces that.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.fake_provider import FakeClock, FakeTickProvider
from tick_support.runs import connect, tick_run
from tick_support.seed import seed_availability, seed_owned_days

from manta_trading.data.tick.acquisition_pass import AwaitTiming, run_pass
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.pass_contract import PassResult, TickOutcome
from manta_trading.data.tick.run_context import TickRun
from manta_trading.data.tick.tick_calendar import product_session_days

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(600),
]

AConn = psycopg.AsyncConnection[Any]
#: The CME_EQUITY calendar is populated from 2020-01-01 23:00 UTC, so asking
#: for session days from midnight on 2020-01-01 is out of range; the first
#: whole UTC day it covers is 2020-01-02.
HISTORY_START = date(2020, 1, 2)
HISTORY_END = date(2026, 9, 1)
START = datetime(2026, 9, 30, 12, tzinfo=UTC)
TIMING = AwaitTiming(budget_seconds=1800, interval_seconds=15)

#: Budgets in seconds. Measured 2026-09-30 from manta9000 against the test
#: cluster, 3 runs over 2,075 session days: first pass 20.9-22.1 s, steady pass
#: 0.17-0.19 s, longest loop gap 29-59 ms. Each budget is roughly 3-10x that,
#: a regression signal rather than a tight fit (see conftest on hardware).
FIRST_PASS_BUDGET_S = 60.0
STEADY_PASS_BUDGET_S = 2.0
MAX_LOOP_GAP_S = 0.25
HEARTBEAT_S = 0.005


class Sleeper:
    """The await phase's sleep: advance the fake clock, yield to the loop."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    async def __call__(self, seconds: float) -> None:
        self.clock.advance(timedelta(seconds=seconds))
        await asyncio.sleep(0)


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


@pytest.fixture
def history(session_migrated_db: str) -> list[date]:
    return product_session_days(session_migrated_db, "ES", HISTORY_START, HISTORY_END)


@pytest.fixture
async def conn(migrated_tick_db: str, history: list[date]) -> AsyncIterator[AConn]:
    seed_owned_days(migrated_tick_db, history)
    seed_availability(migrated_tick_db, history)
    async with connect(migrated_tick_db) as connection:
        yield connection


@pytest.fixture
def run(conn: AConn, tmp_path: Path, session_migrated_db: str) -> TickRun:
    clock = FakeClock(START)
    return tick_run(
        conn,
        FakeTickProvider(clock=clock),
        tmp_path,
        clock,
        timescale_db_url=session_migrated_db,
        tick_spend_ceiling_usd=Decimal("100"),
        tick_spend_30d_ceiling_usd=Decimal("500"),
    )


async def _timed_pass(run: TickRun) -> tuple[PassResult, float, float]:
    """The pass, its wall time and the longest event-loop gap during it."""
    started = time.monotonic()
    with Heartbeat() as beat:
        result = await run_pass(
            run,
            (None, None),
            False,
            DbnFileReader(),
            timing=TIMING,
            sleep=Sleeper(run.clock),  # type: ignore[arg-type]
            free=lambda _: 10**12,
        )
    return result, time.monotonic() - started, beat.max_gap


async def test_whole_history_pass_and_steady_state_stay_in_budget(
    run: TickRun, history: list[date]
) -> None:
    first, first_s, first_gap = await _timed_pass(run)
    steady, steady_s, steady_gap = await _timed_pass(run)
    print(
        f"\nsession days {len(history)}; first {first_s:.2f}s gap {first_gap:.3f}s;"
        f" steady {steady_s:.2f}s gap {steady_gap:.3f}s"
    )
    assert first.outcome is TickOutcome.OK
    assert steady.outcome is TickOutcome.OK
    cursor = await run.conn.execute(
        "SELECT count(*) FILTER (WHERE u.state = 'ingested'), count(*)"
        " FROM tick_archive_unit u JOIN tick_request r USING (request_id)"
        " WHERE r.schema = 'definition'"
    )
    ingested, total = await cursor.fetchone() or (0, 0)
    assert ingested == total == len(history)
    assert first_s < FIRST_PASS_BUDGET_S
    assert steady_s < STEADY_PASS_BUDGET_S
    assert max(first_gap, steady_gap) < MAX_LOOP_GAP_S
