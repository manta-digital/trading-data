"""The whole acquisition pass over a fake provider (slice 224; FR5, FR8, FR10, TD6).

Tick database migrated and seeded with two owned trades days (2024-09-30 and
2024-10-01: wants are two monthly definition requests); the calendar is the
real ``CME_EQUITY``; the provider is ``FakeTickProvider`` writing real DBN day
files; a fake ``sleep`` advances the clock and records each wait.
"""

from __future__ import annotations

import errno
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

from manta_trading.cli.commands.tick_pass_render import print_pass
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick import tick_calendar
from manta_trading.data.tick.acquisition_pass import AwaitTiming, run_pass
from manta_trading.data.tick.constants import BatchJobState, TickSchema, UnitState
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.manifest_pass import (
    insert_pending_request,
    mark_delivered,
    record_submit,
)
from manta_trading.data.tick.manifest_reads import UnitRow, units_by_id
from manta_trading.data.tick.pass_contract import (
    SKIPPED,
    PassResult,
    TickOutcome,
    TickPassPhaseName,
)
from manta_trading.data.tick.planner import PlannedRequest
from manta_trading.data.tick.reset import reset_units
from manta_trading.data.tick.run_context import TickRun
from manta_trading.providers.errors import ProviderTransientError

AConn = psycopg.AsyncConnection[Any]
START = datetime(2026, 9, 29, 12, tzinfo=UTC)
SEPT, OCT = date(2024, 9, 30), date(2024, 10, 1)
NO_WINDOW = (None, None)
TIMING = AwaitTiming(budget_seconds=30, interval_seconds=15)
READER = DbnFileReader()
PhaseName = TickPassPhaseName


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
def world(migrated_tick_db: str) -> None:
    """Two owned days, so the plan is one definition request per month."""
    seed_owned_days(migrated_tick_db, (SEPT, OCT))
    seed_availability(migrated_tick_db, (SEPT, OCT))


@pytest.fixture
def run(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    session_migrated_db: str,
    world: None,
) -> TickRun:
    return tick_run(
        conn,
        provider,
        tmp_path,
        clock,
        timescale_db_url=session_migrated_db,
        tick_spend_ceiling_usd=Decimal("1"),
        tick_spend_30d_ceiling_usd=Decimal("5"),
    )


class Sleeper:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.advance(timedelta(seconds=seconds))


async def _pass(
    run: TickRun,
    *,
    estimate_only: bool = False,
    window: tuple[date | None, date | None] = NO_WINDOW,
    sleeper: Sleeper | None = None,
) -> PassResult:
    assert isinstance(run.clock, FakeClock)
    sleeper = sleeper or Sleeper(run.clock)
    return await run_pass(
        run,
        window,
        estimate_only,
        READER,
        timing=TIMING,
        sleep=sleeper,
        free=lambda _: 10**12,
    )


def _outcomes(result: PassResult) -> dict[PhaseName, Any]:
    return {report.name: report.outcome for report in result.reports}


async def _definition_units(run: TickRun) -> list[UnitRow]:
    cursor = await run.conn.execute(
        "SELECT u.unit_id FROM tick_archive_unit u JOIN tick_request r USING"
        " (request_id) WHERE r.schema = 'definition' ORDER BY u.unit_date, u.unit_id"
    )
    return await units_by_id(run.conn, [row[0] for row in await cursor.fetchall()])


async def _in_flight_definition(
    run: TickRun, provider: FakeTickProvider, day: date, *, deadline: datetime | None
) -> int:
    """A definition request for ``day`` the provider accepted and finished,
    recorded as submitted (and delivered when ``deadline`` is given)."""
    planned = PlannedRequest(
        "ES", request_for(TickSchema.DEFINITION, day, day + timedelta(days=1)), (day,)
    )
    pending = await insert_pending_request(
        run.conn, planned, Decimal("0.001"), run.clock(), {}
    )
    job = provider.add_job(planned.request)
    await record_submit(
        run.conn, pending.request_id, job.job_id, job.ts_received, run.clock()
    )
    if deadline is not None:
        await mark_delivered(
            run.conn,
            pending.request_id,
            cost=Decimal("0.001"),
            record_count=None,
            billed_size=None,
            deadline=deadline,
            now=run.clock(),
        )
    return pending.unit_ids[day]


# -- the whole pass --------------------------------------------------------------------


async def test_a_full_pass_buys_downloads_verifies_and_projects_the_definitions(
    run: TickRun, provider: FakeTickProvider
) -> None:
    result = await _pass(run)
    assert result.outcome is TickOutcome.OK
    assert [r.name for r in result.reports] == list(PhaseName)
    assert len(provider.calls_to("submit_batch")) == 2
    assert {u.state for u in await _definition_units(run)} == {UnitState.INGESTED}
    cursor = await run.conn.execute("SELECT count(*) FROM tick_definition")
    assert await cursor.fetchone() == (2,)  # one row per instrument, re-sends no-op
    for job in ("GLBX-FAKE-0001", "GLBX-FAKE-0002"):
        assert (run.archive_root / job / "manifest.json").is_file()
    definitions = result.reports[-1].summary
    assert (definitions["projected"], definitions["inserted"]) == (2, 2)
    assert definitions["noops"] == 2  # the second day re-sent both, unchanged
    assert provider.calls_to("fetch_range") == []


async def test_the_report_renders_a_real_pass_summary(
    run: TickRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """The summaries are dicts; this pins the keys the renderer reads to the
    keys the phases write, so a rename fails here instead of in the report."""
    print_pass(await _pass(run), 0, json_mode=False)
    out = capsys.readouterr().out
    for line in (
        "unknown submits: 0 matched",  # reconcile
        "GLBX.MDP3: edge",  # availability
        "wanted ES/definition: 2 day(s)",  # purchase: per-key wants
        "planned $",  # purchase: totals and ceilings
        "verdict: allowed",
        "submitted job GLBX-FAKE-0001",
        "units downloaded: 2",  # await: a delivery counter
        "units projected: 2",  # definitions
        "Outcome: ok",
    ):
        assert line in out, f"{line!r} missing from:\n{out}"


async def test_a_second_pass_after_success_plans_nothing(
    run: TickRun, provider: FakeTickProvider
) -> None:
    await _pass(run)
    provider.calls.clear()
    again = await _pass(run)
    assert again.outcome is TickOutcome.OK
    purchase = again.reports[2].summary
    assert purchase["verdict"] == "nothing_to_buy" and purchase["requests"] == []
    assert provider.calls_to("submit_batch") == []


async def test_two_passes_after_an_unknown_submit_make_exactly_one_submit(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    session_migrated_db: str,
    migrated_tick_db: str,
) -> None:
    seed_owned_days(migrated_tick_db, (SEPT,))  # one wanted request
    seed_availability(migrated_tick_db, (SEPT,))
    run = tick_run(
        conn,
        provider,
        tmp_path,
        clock,
        timescale_db_url=session_migrated_db,
        tick_spend_ceiling_usd=Decimal("1"),
        tick_spend_30d_ceiling_usd=Decimal("5"),
    )
    provider.submit_script.extend([Submit.UNKNOWN_ACCEPTED])
    first = await _pass(run)
    assert first.outcome is TickOutcome.PROVIDER_ABORT
    assert _outcomes(first)[PhaseName.AWAIT] == SKIPPED
    clock.advance(timedelta(minutes=1))
    second = await _pass(run)
    assert second.outcome is TickOutcome.OK
    assert len(provider.calls_to("submit_batch")) == 1  # the job was matched
    assert {u.state for u in await _definition_units(run)} == {UnitState.INGESTED}


async def test_a_reset_unresolved_row_searches_again_then_resubmits_the_same_row(
    conn: AConn,
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    session_migrated_db: str,
    migrated_tick_db: str,
) -> None:
    seed_owned_days(migrated_tick_db, (SEPT,))
    seed_availability(migrated_tick_db, (SEPT,))
    run = tick_run(
        conn,
        provider,
        tmp_path,
        clock,
        timescale_db_url=session_migrated_db,
        tick_spend_ceiling_usd=Decimal("1"),
        tick_spend_30d_ceiling_usd=Decimal("5"),
    )
    provider.submit_script.extend([Submit.UNKNOWN_LOST])
    await _pass(run)  # nothing accepted, outcome unknown
    clock.advance(timedelta(hours=2))
    exhausted = await _pass(run)
    assert exhausted.outcome is TickOutcome.PARTIAL  # exhausted: `reset` to re-submit
    assert len(provider.calls_to("submit_batch")) == 1  # never re-submitted alone
    (unit,) = await _definition_units(run)
    assert unit.fetch_status is FetchStatus.RETRY_EXHAUSTED
    await reset_units(run, [unit.unit_id])
    provider.calls.clear()
    third = await _pass(run)
    assert third.outcome is TickOutcome.OK
    methods = [call.method for call in provider.calls]
    assert methods.index("batch_jobs_since") < methods.index("submit_batch")
    (again,) = await _definition_units(run)
    assert again.unit_id == unit.unit_id and again.state is UnitState.INGESTED
    cursor = await run.conn.execute(
        "SELECT count(*) FROM tick_request WHERE schema = 'definition'"
    )
    assert await cursor.fetchone() == (1,)


async def test_a_delivered_job_is_downloaded_before_the_first_new_submit(
    run: TickRun, provider: FakeTickProvider
) -> None:
    unit_id = await _in_flight_definition(run, provider, SEPT, deadline=None)
    result = await _pass(run)
    assert result.outcome is TickOutcome.OK
    methods = [call.method for call in provider.calls]
    assert methods.index("download_batch") < methods.index("submit_batch")
    assert len(provider.calls_to("submit_batch")) == 1  # only October is new
    (sept, _) = await _definition_units(run)
    assert sept.unit_id == unit_id and sept.state is UnitState.INGESTED


async def test_a_job_still_processing_at_budget_end_is_in_flight_with_its_deadline(
    run: TickRun, provider: FakeTickProvider, clock: FakeClock
) -> None:
    provider.initial_state = BatchJobState.PROCESSING
    provider.polls_until_done = None
    sleeper = Sleeper(clock)
    result = await _pass(run, sleeper=sleeper)
    assert result.outcome is TickOutcome.IN_FLIGHT
    assert _outcomes(result)[PhaseName.AWAIT] is TickOutcome.IN_FLIGHT
    summary = result.reports[3].summary
    assert {j["state"] for j in summary["in_flight"]} == {"processing"}
    assert len(summary["in_flight"]) == 2
    expected = (START + timedelta(days=30)).isoformat()
    assert {j["deadline"] for j in summary["in_flight"]} == {expected}
    assert sleeper.slept == [15, 15]  # the budget, in poll intervals
    assert summary["waited_seconds"] == 30
    assert {u.state for u in await _definition_units(run)} == {UnitState.SUBMITTED}


async def test_a_swept_expired_day_is_rebought_with_supersession_links(
    run: TickRun, provider: FakeTickProvider
) -> None:
    old = await _in_flight_definition(
        run, provider, SEPT, deadline=START - timedelta(days=1)
    )
    result = await _pass(run)
    assert result.outcome is TickOutcome.OK
    assert result.reports[0].summary["swept_expired"] == 1
    cursor = await run.conn.execute(
        "SELECT superseded_by_unit_id, reopened_at IS NOT NULL FROM tick_archive_unit"
        " WHERE unit_id = %s",
        (old,),
    )
    row = await cursor.fetchone()
    assert row is not None
    superseded_by, reopened = row
    assert reopened and superseded_by is not None
    cursor = await run.conn.execute(
        "SELECT repurchase_of_unit_id, state FROM tick_archive_unit WHERE unit_id = %s",
        (superseded_by,),
    )
    assert await cursor.fetchone() == (old, UnitState.INGESTED.value)
    assert len(provider.calls_to("submit_batch")) == 2  # September again, and October


async def test_enospc_during_reconcile_download_aborts_and_skips_the_rest(
    run: TickRun, provider: FakeTickProvider
) -> None:
    await _in_flight_definition(run, provider, SEPT, deadline=None)
    provider.download_errors.append(OSError(errno.ENOSPC, "No space left on device"))
    result = await _pass(run)
    assert result.outcome is TickOutcome.STORAGE_ABORT
    outcomes = _outcomes(result)
    assert outcomes[PhaseName.RECONCILE] is TickOutcome.STORAGE_ABORT
    assert [outcomes[n] for n in list(PhaseName)[1:]] == [SKIPPED] * 4
    assert str(errno.ENOSPC) in (result.reports[0].error or "")
    assert provider.calls_to("submit_batch") == []


async def test_a_provider_outage_in_reconcile_aborts_before_anything_is_bought(
    run: TickRun, provider: FakeTickProvider
) -> None:
    await _in_flight_definition(run, provider, SEPT, deadline=None)
    provider.download_errors.append(ProviderTransientError("down"))
    result = await _pass(run)
    assert result.outcome is TickOutcome.PROVIDER_ABORT
    assert provider.calls_to("submit_batch") == []


async def test_an_unreachable_calendar_still_delivers_but_buys_nothing(
    run: TickRun, provider: FakeTickProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_id = await _in_flight_definition(run, provider, SEPT, deadline=None)

    def unreachable(*args: Any) -> list[date]:
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    monkeypatch.setattr(tick_calendar, "session_days", unreachable)
    result = await _pass(run)
    outcomes = _outcomes(result)
    assert outcomes[PhaseName.RECONCILE] is TickOutcome.OK  # downloaded, verified
    assert outcomes[PhaseName.AVAILABILITY] is TickOutcome.OK
    assert outcomes[PhaseName.PURCHASE] is TickOutcome.STORAGE_ABORT
    assert "CME_EQUITY unavailable" in (result.reports[2].error or "")
    assert outcomes[PhaseName.AWAIT] == outcomes[PhaseName.DEFINITIONS] == SKIPPED
    (unit,) = await units_by_id(run.conn, [unit_id])
    assert unit.state is UnitState.VERIFIED
    assert provider.calls_to("submit_batch") == []


async def test_estimate_only_plans_and_writes_nothing(
    run: TickRun, provider: FakeTickProvider
) -> None:
    result = await _pass(run, estimate_only=True)
    assert result.outcome is TickOutcome.OK
    assert provider.calls_to("submit_batch") == []
    assert len(result.reports[2].summary["requests"]) == 2
    assert result.reports[3].summary == {"skipped": "estimate-only"}
    assert await _definition_units(run) == []
