"""The purchase phase over a fake provider (slice 224; FR3-FR5, TD7, TD9).

The tick database is migrated and seeded with owned tier days (as adoption
leaves them) and day conditions; the calendar is the real ``CME_EQUITY`` from
``session_migrated_db``. The fake provider records every call, so "one submit"
and "no submit" are assertions on ``calls``.

Wants: definitions for the owned trades days — 2024-09-30 (Mon) and 2024-10-01
(Tue), two requests because the run splits at the month boundary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg_pool import PoolTimeout
from tick_support.fake_provider import (
    FakeClock,
    FakeTickProvider,
    Submit,
    request_for,
)
from tick_support.runs import connect, tick_run
from tick_support.seed import seed_availability, seed_owned_days

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick import tick_calendar
from manta_trading.data.tick.constants import (
    TICK_SPEND_30D_CEILING_ENV,
    TICK_SPEND_CEILING_ENV,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.manifest_reads import UnitRow
from manta_trading.data.tick.pass_contract import PhaseReport, TickOutcome
from manta_trading.data.tick.purchase_phase import PassState, PurchasePhase
from manta_trading.data.tick.run_context import TickRun

AConn = psycopg.AsyncConnection[Any]
START = datetime(2026, 9, 29, 12, tzinfo=UTC)
OWNED = (date(2024, 9, 30), date(2024, 10, 1))
NO_WINDOW = (None, None)
PER_PASS, CAP_30D = Decimal("1"), Decimal("5")


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


def _run(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    calendar_url: str,
    **settings: Any,
) -> TickRun:
    ceilings: dict[str, Any] = {
        "tick_spend_ceiling_usd": PER_PASS,
        "tick_spend_30d_ceiling_usd": CAP_30D,
    }
    return tick_run(
        conn,
        provider,
        tmp_path,
        clock,
        timescale_db_url=calendar_url,
        **(ceilings | settings),
    )


@pytest.fixture
def run(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    session_migrated_db: str,
) -> TickRun:
    return _run(conn, provider, clock, tmp_path, session_migrated_db)


async def _phase(
    run: TickRun,
    *,
    estimate_only: bool = False,
    free: int = 10**12,
    state: PassState | None = None,
) -> PhaseReport:
    phase = PurchasePhase(
        NO_WINDOW, estimate_only, state or PassState(), free=lambda _: free
    )
    return await phase.run(run)


async def _units(run: TickRun) -> list[UnitRow]:
    from manta_trading.data.tick.manifest_reads import open_units_in  # noqa: F401

    cursor = await run.conn.execute(
        "SELECT u.unit_id FROM tick_archive_unit u JOIN tick_request r USING"
        " (request_id) WHERE r.schema = 'definition' ORDER BY u.unit_date"
    )
    from manta_trading.data.tick.manifest_reads import units_by_id

    return await units_by_id(run.conn, [row[0] for row in await cursor.fetchall()])


def _by_day(units: list[UnitRow]) -> dict[date, UnitRow]:
    return {u.unit_date: u for u in units}


async def test_two_definition_requests_are_submitted_and_recorded(
    run: TickRun, provider: FakeTickProvider
) -> None:
    state = PassState()
    report = await _phase(run, state=state)
    assert report.outcome is TickOutcome.OK
    assert len(provider.calls_to("submit_batch")) == 2
    assert [j.job_id for j in state.submitted] == [f"GLBX-FAKE-000{n}" for n in (1, 2)]
    units = await _units(run)
    assert {(u.state, u.fetch_status, u.attempt_count) for u in units} == {
        (UnitState.SUBMITTED, FetchStatus.UNKNOWN, 0)
    }
    summary = report.summary
    assert summary["verdict"] == "allowed"
    assert [r["schema"] for r in summary["requests"]] == ["definition"] * 2
    assert summary["wanted_days"] == {"ES/definition": 2}
    assert len(summary["submitted"]) == 2


async def test_a_4xx_refusal_exhausts_its_units_and_the_second_is_submitted(
    run: TickRun, provider: FakeTickProvider
) -> None:
    provider.submit_script.extend([Submit.REFUSE])
    report = await _phase(run)
    assert report.outcome is TickOutcome.PARTIAL
    assert len(provider.calls_to("submit_batch")) == 2
    by_day = _by_day(await _units(run))
    first, second = by_day[OWNED[0]], by_day[OWNED[1]]
    assert (first.state, first.fetch_status) == (
        UnitState.REQUESTED,
        FetchStatus.RETRY_EXHAUSTED,
    )
    assert "submit refused" in (first.failure_reason or "")
    assert second.state is UnitState.SUBMITTED


async def test_a_429_marks_retryable_and_stops_with_provider_abort(
    run: TickRun, provider: FakeTickProvider
) -> None:
    provider.submit_script.extend([Submit.RATE_LIMIT])
    report = await _phase(run)
    assert report.outcome is TickOutcome.PROVIDER_ABORT
    assert len(provider.calls_to("submit_batch")) == 1
    units = _by_day(await _units(run))
    assert (units[OWNED[0]].state, units[OWNED[0]].fetch_status) == (
        UnitState.REQUESTED,
        FetchStatus.FAILED_RETRYABLE,
    )
    assert units[OWNED[0]].attempt_count == 1
    assert OWNED[1] not in units  # the second request was never even written


async def test_an_unknown_outcome_stops_after_exactly_one_submit(
    run: TickRun, provider: FakeTickProvider
) -> None:
    provider.submit_script.extend([Submit.UNKNOWN_ACCEPTED])
    report = await _phase(run)
    assert report.outcome is TickOutcome.PROVIDER_ABORT
    assert len(provider.calls_to("submit_batch")) == 1
    (unit,) = [u for u in await _units(run) if u.unit_date == OWNED[0]]
    assert (unit.state, unit.fetch_status) == (
        UnitState.REQUESTED,
        FetchStatus.FAILED_RETRYABLE,
    )
    assert unit.attempt_count == 1  # counted once, before the paid call
    assert "unknown" in (unit.failure_reason or "")
    assert report.error is not None


@pytest.mark.parametrize(
    "missing",
    [
        {"tick_spend_ceiling_usd": None},
        {"tick_spend_30d_ceiling_usd": None},
        {"tick_spend_ceiling_usd": None, "tick_spend_30d_ceiling_usd": None},
    ],
)
async def test_an_absent_ceiling_refuses_naming_both_variables(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    session_migrated_db: str,
    missing: dict[str, Any],
) -> None:
    run = _run(conn, provider, clock, tmp_path, session_migrated_db, **missing)
    report = await _phase(run)
    assert report.outcome is TickOutcome.REFUSED
    assert provider.calls_to("submit_batch") == []
    text = " ".join(report.summary["reasons"])
    assert TICK_SPEND_CEILING_ENV in text and TICK_SPEND_30D_CEILING_ENV in text
    assert report.summary["verdict"] == "refused"


async def test_estimate_only_reports_the_plan_and_submits_nothing(
    run: TickRun, provider: FakeTickProvider
) -> None:
    report = await _phase(run, estimate_only=True)
    assert report.outcome is TickOutcome.OK
    assert provider.calls_to("submit_batch") == []
    assert report.summary["verdict"] == "estimate_only"
    assert len(report.summary["requests"]) == 2
    assert Decimal(report.summary["planned_usd"]) == Decimal("0.002")


async def test_a_space_shortfall_refuses_naming_it(
    run: TickRun, provider: FakeTickProvider
) -> None:
    report = await _phase(run, free=1500)  # the plan needs 2 × 1000 bytes
    assert report.outcome is TickOutcome.REFUSED
    assert provider.calls_to("submit_batch") == []
    assert any("500 short" in reason for reason in report.summary["reasons"])


async def test_a_listed_job_no_row_holds_counts_and_is_named(
    run: TickRun, provider: FakeTickProvider, clock: FakeClock
) -> None:
    portal = provider.add_job(
        request_for(TickSchema.TBBO, date(2024, 8, 1), date(2024, 8, 2)),
        cost=Decimal("4.999"),
        ts_received=START - timedelta(days=2),
    )
    report = await _phase(run)
    assert report.outcome is TickOutcome.REFUSED
    assert provider.calls_to("submit_batch") == []
    assert portal.job_id in " ".join(report.summary["reasons"])
    assert [j["job_id"] for j in report.summary["unheld_jobs"]] == [portal.job_id]


async def test_a_listed_job_a_row_holds_is_not_counted(
    run: TickRun, provider: FakeTickProvider, conn: AConn
) -> None:
    held = provider.add_job(
        request_for(TickSchema.TBBO, date(2024, 8, 1), date(2024, 8, 2)),
        cost=Decimal("4.999"),
        ts_received=START - timedelta(days=2),
    )
    await conn.execute(
        "UPDATE tick_request SET provider_job_id = %s, committed_at = %s,"
        " actual_cost_usd = 0.01 WHERE is_adopted",
        (held.job_id, held.ts_received),
    )
    report = await _phase(run)
    assert report.outcome is TickOutcome.OK
    assert report.summary["unheld_jobs"] == []
    assert len(provider.calls_to("submit_batch")) == 2


async def test_a_range_outside_the_calendar_is_a_storage_abort_naming_it(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    session_migrated_db: str,
    migrated_tick_db: str,
) -> None:
    early = (date(2019, 12, 2), date(2019, 12, 3))  # before CME_EQUITY's span
    await conn.execute("DELETE FROM tick_archive_unit")
    await conn.execute("DELETE FROM tick_request")
    await conn.execute("DELETE FROM tick_day_condition")
    await conn.execute("DELETE FROM tick_dataset_edge")
    seed_owned_days(migrated_tick_db, early)
    seed_availability(migrated_tick_db, early)
    run = _run(conn, provider, clock, tmp_path, session_migrated_db)
    report = await _phase(run)
    assert report.outcome is TickOutcome.STORAGE_ABORT
    assert "CME_EQUITY" in (report.error or "")
    assert provider.calls_to("submit_batch") == []


async def test_an_unreachable_calendar_is_a_storage_abort_naming_it(
    run: TickRun, provider: FakeTickProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(*args: Any) -> list[date]:
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    monkeypatch.setattr(tick_calendar, "session_days", unreachable)
    report = await _phase(run)
    assert report.outcome is TickOutcome.STORAGE_ABORT
    assert "CME_EQUITY unavailable" in (report.error or "")
    assert provider.calls_to("submit_batch") == []


async def test_no_wants_is_ok_and_never_touches_the_calendar(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    session_migrated_db: str,
) -> None:
    await conn.execute("DELETE FROM tick_archive_unit")
    await conn.execute("DELETE FROM tick_request")
    run = _run(conn, provider, clock, tmp_path, session_migrated_db)
    report = await _phase(run)
    assert report.outcome is TickOutcome.OK
    assert report.summary["verdict"] == "nothing_to_buy"
    assert provider.calls_to("submit_batch") == []
    # the report shows the configured ceilings even when nothing is bought
    assert report.summary["per_pass_ceiling_usd"] == str(PER_PASS)
    assert report.summary["cap_30d_usd"] == str(CAP_30D)


async def test_a_reset_row_is_resubmitted_on_the_same_request(
    run: TickRun, provider: FakeTickProvider
) -> None:
    provider.submit_script.extend([Submit.UNKNOWN_LOST])
    first = await _phase(run)  # unknown on the first request → abort
    assert first.outcome is TickOutcome.PROVIDER_ABORT
    (before,) = [u for u in await _units(run) if u.unit_date == OWNED[0]]
    await run.conn.execute(
        "UPDATE tick_archive_unit SET fetch_status = 'UNKNOWN',"
        " failure_reason = NULL, attempt_count = 0 WHERE unit_id = %s",
        (before.unit_id,),
    )  # what `reset` does to an exhausted unit
    report = await _phase(run)
    assert report.outcome is TickOutcome.OK
    summary_requests = report.summary["requests"]
    assert sum(1 for r in summary_requests if r["resubmit"]) == 1
    (after,) = [u for u in await _units(run) if u.unit_date == OWNED[0]]
    assert after.unit_id == before.unit_id  # the same row, not a new one
    assert after.state is UnitState.SUBMITTED
    cursor = await run.conn.execute(
        "SELECT count(*) FROM tick_request WHERE schema = 'definition'"
    )
    assert await cursor.fetchone() == (2,)
