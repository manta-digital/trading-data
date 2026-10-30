"""The purchase plan classifies planned, resubmitted and unheld requests (slice 300).

``is_plan_included`` is called once per planned item; the guard sums by it and
the submit writes it to the row. Unheld provider jobs are judged at their own
``ts_received``. The fixtures follow ``test_tick_purchase_phase.py``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.fake_provider import FakeClock, FakeTickProvider, request_for
from tick_support.runs import connect, tick_run
from tick_support.seed import seed_availability, seed_owned_days
from tick_support.universe import use_untiered

from manta_trading.data.tick import purchase_plan
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.pass_contract import TickOutcome
from manta_trading.data.tick.planner import PlannedRequest
from manta_trading.data.tick.provider import TickRequest
from manta_trading.data.tick.purchase_phase import PassState, PurchasePhase
from manta_trading.data.tick.run_context import TickRun

AConn = psycopg.AsyncConnection[Any]
START = datetime(2026, 9, 29, 12, tzinfo=UTC)
TODAY = START.date()
OWNED = (date(2024, 9, 30), date(2024, 10, 1))
TINY = Decimal("0.0000001")  # below any request's cost: only inclusion fits


@pytest.fixture(autouse=True)
def untiered(monkeypatch: pytest.MonkeyPatch) -> None:
    use_untiered(monkeypatch)


@pytest.fixture(autouse=True)
def _seeded(migrated_tick_db: str) -> None:
    seed_owned_days(migrated_tick_db, OWNED)
    seed_availability(migrated_tick_db, OWNED)


@pytest.fixture
async def conn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with connect(migrated_tick_db) as connection:
        yield connection


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def provider(clock: FakeClock) -> FakeTickProvider:
    return FakeTickProvider(clock=clock)


@pytest.fixture
def make_run(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    session_migrated_db: str,
) -> Any:
    def factory(plan: str | None, per_pass: Decimal = Decimal("1")) -> TickRun:
        return tick_run(
            conn,
            provider,
            tmp_path,
            clock,
            timescale_db_url=session_migrated_db,
            tick_spend_ceiling_usd=per_pass,
            tick_spend_30d_ceiling_usd=Decimal("5"),
            tick_databento_plan=plan,
        )

    return factory


def _request(schema: TickSchema, days_back: int, length: int = 1) -> TickRequest:
    start = TODAY - timedelta(days=days_back)
    return request_for(schema, start, start + timedelta(days=length))


async def _classified(run: TickRun, *requests: TickRequest) -> list[bool]:
    planned = [PlannedRequest("ES", r, (r.start,)) for r in requests]
    items = await purchase_plan._costed(run, planned, START)
    return [item.included for item in items]


async def test_planned_items_are_classified_by_schema_age_and_boundary(
    make_run: Any,
) -> None:
    run = make_run("standard")
    assert await _classified(
        run,
        _request(TickSchema.TBBO, 30),  # recent
        _request(TickSchema.TBBO, 400),  # older than the window
        _request(TickSchema.TBBO, 367, length=5),  # straddles the boundary
        _request(TickSchema.DEFINITION, 2000),  # definitions: all history
    ) == [True, False, False, True]


async def test_with_no_plan_nothing_is_included(make_run: Any) -> None:
    run = make_run(None)
    assert await _classified(
        run, _request(TickSchema.TBBO, 30), _request(TickSchema.DEFINITION, 30)
    ) == [False, False]


async def test_unheld_jobs_are_judged_at_their_own_ts_received(
    make_run: Any, provider: FakeTickProvider
) -> None:
    received = START - timedelta(days=2)
    recent = provider.add_job(
        _request(TickSchema.TBBO, 30), cost=Decimal("2"), ts_received=received
    )
    definition = provider.add_job(
        _request(TickSchema.DEFINITION, 2000), cost=Decimal("3"), ts_received=received
    )
    old = provider.add_job(
        _request(TickSchema.TBBO, 400), cost=Decimal("4"), ts_received=received
    )
    elsewhere = provider.add_job(
        dataclasses.replace(_request(TickSchema.TBBO, 30), dataset="XNAS.ITCH"),
        cost=Decimal("5"),
        ts_received=received,
    )
    standard = await purchase_plan._unheld(make_run("standard"), frozenset())
    assert {job.job_id: job.cost for job in standard} == {
        old.job_id: Decimal("4"),
        elsewhere.job_id: Decimal("5"),
    }
    unplanned = await purchase_plan._unheld(make_run(None), frozenset())
    assert {job.job_id for job in unplanned} == {
        recent.job_id,
        definition.job_id,
        old.job_id,
        elsewhere.job_id,
    }


async def test_a_job_a_row_holds_is_never_unheld_even_when_included(
    make_run: Any, provider: FakeTickProvider
) -> None:
    job = provider.add_job(
        _request(TickSchema.TBBO, 400), cost=Decimal("4"), ts_received=START
    )
    held = frozenset({job.job_id})
    assert await purchase_plan._unheld(make_run(None), held) == []


async def _submit(run: TickRun) -> Any:
    phase = PurchasePhase((None, None), False, PassState(), free=lambda _: 10**12)
    return await phase.run(run)


async def test_the_row_marker_equals_the_value_the_guard_used(
    make_run: Any, conn: AConn
) -> None:
    """A ceiling below any list price allows the purchase only because the plan
    includes both definition requests, and both rows record ``TRUE``."""
    report = await _submit(make_run("standard", per_pass=TINY))
    assert report.outcome is TickOutcome.OK
    assert Decimal(report.summary["planned_usd"]) == 0
    cursor = await conn.execute(
        "SELECT is_plan_included FROM tick_request WHERE NOT is_adopted"
    )
    assert [row[0] for row in await cursor.fetchall()] == [True, True]


async def test_with_no_plan_the_same_ceiling_refuses_and_nothing_is_written(
    make_run: Any, conn: AConn
) -> None:
    report = await _submit(make_run(None, per_pass=TINY))
    assert report.outcome is TickOutcome.REFUSED
    cursor = await conn.execute(
        "SELECT count(*) FROM tick_request WHERE NOT is_adopted"
    )
    assert await cursor.fetchone() == (0,)
