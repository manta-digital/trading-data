"""Reconcile, delivery and expiry over a fake provider (slice 224; FR5, FR6).

Requests reach *requested* and *submitted* through the manifest functions, the
provider is ``FakeTickProvider`` (real DBN day files on disk), and the calendar
is not involved (TD6: delivery needs none).
"""

from __future__ import annotations

import errno
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.fake_provider import (
    FakeClock,
    FakeJob,
    FakeTickProvider,
    request_for,
)
from tick_support.runs import connect, tick_run

from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.adopt_files import TickArchiveWriteError
from manta_trading.data.tick.constants import (
    TICK_SUBMIT_RESOLVE_AGE,
    BatchJobState,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.in_flight import (
    EXHAUSTED_REASON,
    UNRESOLVED_REASON,
    advance,
    resolve_unsubmitted,
    sweep_expired,
)
from manta_trading.data.tick.manifest_pass import (
    PendingRequest,
    insert_pending_request,
    mark_delivered,
    record_submit,
)
from manta_trading.data.tick.manifest_reads import UnitRow, units_of_request
from manta_trading.data.tick.planner import PlannedRequest
from manta_trading.data.tick.run_context import TickRun
from manta_trading.providers.errors import ProviderTransientError

AConn = psycopg.AsyncConnection[Any]
START = datetime(2026, 9, 29, 12, tzinfo=UTC)
DAYS = (date(2024, 9, 3), date(2024, 9, 4), date(2024, 9, 5))  # Tue-Thu
ESTIMATE = Decimal("0.003")
READER = DbnFileReader()


def _planned(
    schema: TickSchema = TickSchema.TRADES, days: tuple[date, ...] = DAYS
) -> PlannedRequest:
    request = request_for(schema, days[0], days[-1] + timedelta(days=1))
    return PlannedRequest("ES", request, days)


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
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def run(
    conn: AConn, provider: FakeTickProvider, archive: Path, clock: FakeClock
) -> TickRun:
    return tick_run(conn, provider, archive, clock)


async def _pending(
    run: TickRun, planned: PlannedRequest | None = None
) -> PendingRequest:
    return await insert_pending_request(
        run.conn, planned or _planned(), ESTIMATE, run.clock(), {}
    )


async def _submitted(
    run: TickRun, provider: FakeTickProvider, planned: PlannedRequest | None = None
) -> tuple[PendingRequest, FakeJob]:
    """A request the provider accepted and the manifest recorded."""
    plan = planned or _planned()
    pending = await _pending(run, plan)
    job = provider.add_job(plan.request)
    await record_submit(
        run.conn, pending.request_id, job.job_id, job.ts_received, run.clock()
    )
    return pending, job


async def _units(run: TickRun, pending: PendingRequest) -> list[UnitRow]:
    return await units_of_request(run.conn, pending.request_id)


def _states(units: list[UnitRow]) -> set[tuple[UnitState, FetchStatus]]:
    return {(u.state, u.fetch_status) for u in units}


# -- unknown submits ---------------------------------------------------------------


async def test_unknown_submit_then_a_listed_job_is_matched_without_a_submit(
    run: TickRun, provider: FakeTickProvider
) -> None:
    pending = await _pending(run)
    job = provider.add_job(_planned().request)  # accepted; the answer was lost
    tally = await resolve_unsubmitted(run)
    assert tally.matched == 1
    assert _states(await _units(run, pending)) == {
        (UnitState.SUBMITTED, FetchStatus.UNKNOWN)
    }
    cursor = await run.conn.execute(
        "SELECT provider_job_id, committed_at FROM tick_request WHERE request_id = %s",
        (pending.request_id,),
    )
    assert await cursor.fetchone() == (job.job_id, job.ts_received)
    assert provider.calls_to("submit_batch") == []


async def test_both_crash_points_leave_one_attempt_and_neither_resubmits(
    run: TickRun, provider: FakeTickProvider
) -> None:
    accepted_plan = _planned(days=DAYS[:1])
    never_plan = _planned(days=DAYS[1:])
    accepted = await _pending(run, accepted_plan)  # crash after the accepted submit
    never = await _pending(run, never_plan)  # crash before submit_batch
    for pending in (accepted, never):
        for unit in await _units(run, pending):
            assert unit.attempt_count == 1 and unit.last_attempt_at == START
    provider.add_job(accepted_plan.request)
    tally = await resolve_unsubmitted(run)
    assert (tally.matched, tally.retryable) == (1, 1)
    assert _states(await _units(run, accepted)) == {
        (UnitState.SUBMITTED, FetchStatus.UNKNOWN)
    }
    held = await _units(run, never)
    assert _states(held) == {(UnitState.REQUESTED, FetchStatus.FAILED_RETRYABLE)}
    assert {u.failure_reason for u in held} == {UNRESOLVED_REASON}
    assert provider.calls_to("submit_batch") == []


async def test_no_listed_job_stays_retryable_then_exhausts_after_the_age(
    run: TickRun, provider: FakeTickProvider, clock: FakeClock
) -> None:
    pending = await _pending(run)
    await resolve_unsubmitted(run)
    assert _states(await _units(run, pending)) == {
        (UnitState.REQUESTED, FetchStatus.FAILED_RETRYABLE)
    }
    clock.advance(TICK_SUBMIT_RESOLVE_AGE - timedelta(minutes=1))
    tally = await resolve_unsubmitted(run)
    assert tally.retryable == 1
    clock.advance(timedelta(minutes=2))
    tally = await resolve_unsubmitted(run)
    assert tally.exhausted == 1
    units = await _units(run, pending)
    assert _states(units) == {(UnitState.REQUESTED, FetchStatus.RETRY_EXHAUSTED)}
    assert {u.failure_reason for u in units} == {EXHAUSTED_REASON}
    assert "reset" in EXHAUSTED_REASON
    assert provider.calls_to("submit_batch") == []
    again = await resolve_unsubmitted(run)  # exhausted rows are left alone
    assert (again.retryable, again.exhausted) == (0, 0)


async def test_a_job_another_row_holds_is_not_matched_twice(
    run: TickRun, provider: FakeTickProvider
) -> None:
    await _submitted(run, provider)  # this row already holds the job
    twin = await _pending(run)  # an identical request, unresolved
    tally = await resolve_unsubmitted(run)
    assert tally.matched == 0
    assert _states(await _units(run, twin)) == {
        (UnitState.REQUESTED, FetchStatus.FAILED_RETRYABLE)
    }


# -- expiry and job states -----------------------------------------------------------


async def test_a_past_deadline_unit_is_swept_and_reopened(
    run: TickRun, provider: FakeTickProvider, clock: FakeClock
) -> None:
    pending, job = await _submitted(run, provider)
    await mark_delivered(
        run.conn,
        pending.request_id,
        cost=ESTIMATE,
        record_count=None,
        billed_size=None,
        deadline=START + timedelta(days=30),
        now=START,
    )
    assert await sweep_expired(run) == 0  # inside the retention window
    clock.advance(timedelta(days=31))
    assert await sweep_expired(run) == len(DAYS)
    for unit in await _units(run, pending):
        assert unit.fetch_status is FetchStatus.RETRY_EXHAUSTED
        assert unit.reopened_at == clock()
        assert (unit.failure_reason or "").startswith("retention expired at ")
    assert await sweep_expired(run) == 0  # already reopened


async def test_delivered_units_without_a_file_past_the_deadline_are_reopened(
    run: TickRun, provider: FakeTickProvider, clock: FakeClock
) -> None:
    pending, job = await _submitted(run, provider)
    provider.download_errors.extend([ProviderTransientError("down")] * MAX_RETRY_COUNT)
    for _ in range(MAX_RETRY_COUNT):
        with pytest.raises(ProviderTransientError):
            await advance(run, READER)
    clock.advance(timedelta(days=31))
    assert await sweep_expired(run) == len(DAYS)
    for unit in await _units(run, pending):
        assert unit.fetch_status is FetchStatus.RETRY_EXHAUSTED
        assert unit.reopened_at == clock()
        assert unit.failure_reason is not None
        assert unit.failure_reason.startswith("retention expired at ")


@pytest.mark.parametrize(
    ("job_state", "reason"),
    [(BatchJobState.EXPIRED, "expired"), (None, "state")],
)
async def test_expired_and_unknown_job_states_exhaust_naming_the_state(
    run: TickRun,
    provider: FakeTickProvider,
    job_state: BatchJobState | None,
    reason: str,
) -> None:
    pending, job = await _submitted(run, provider)
    provider.jobs[job.job_id].state = BatchJobState.QUEUED
    if job_state is None:
        provider.jobs[job.job_id].unknown_state = "cancelled"
    else:
        provider.jobs[job.job_id].state = job_state
    tally = await advance(run, READER)
    units = await _units(run, pending)
    assert {u.fetch_status for u in units} == {FetchStatus.RETRY_EXHAUSTED}
    assert all(reason in (u.failure_reason or "") for u in units)
    if job_state is None:
        assert "cancelled" in (units[0].failure_reason or "")
        assert tally.refused == 1 and all(u.reopened_at is None for u in units)
    else:
        assert tally.expired == 1 and all(u.reopened_at is not None for u in units)


# -- download order, holes, retries ----------------------------------------------------


async def test_two_delivered_jobs_download_earliest_deadline_first(
    run: TickRun, provider: FakeTickProvider, clock: FakeClock
) -> None:
    late_plan = _planned(days=DAYS[:1])
    early_plan = _planned(days=DAYS[1:])
    late_pending, late_job = await _submitted(run, provider, late_plan)
    early_pending, early_job = await _submitted(run, provider, early_plan)
    provider.jobs[late_job.job_id].ts_received = START + timedelta(hours=5)
    provider.jobs[early_job.job_id].ts_received = START
    await advance(run, READER)
    assert provider.calls_to("download_batch") == [early_job.job_id, late_job.job_id]


async def test_a_missing_day_file_is_a_provider_hole(
    run: TickRun, provider: FakeTickProvider
) -> None:
    pending, _ = await _submitted(run, provider)
    provider.missing_days.add(DAYS[1])
    tally = await advance(run, READER)
    by_day = {u.unit_date: u for u in await _units(run, pending)}
    assert by_day[DAYS[1]].fetch_status is FetchStatus.PROVIDER_HOLE
    assert by_day[DAYS[1]].state is UnitState.DELIVERED
    assert {by_day[d].state for d in (DAYS[0], DAYS[2])} == {UnitState.VERIFIED}
    assert (tally.downloaded, tally.holed, tally.verified) == (2, 1, 2)


async def test_transient_download_failures_count_one_attempt_each_the_fifth_exhausts(
    run: TickRun, provider: FakeTickProvider
) -> None:
    pending, _ = await _submitted(run, provider)
    provider.download_errors.extend([ProviderTransientError("down")] * MAX_RETRY_COUNT)
    for attempt in range(1, MAX_RETRY_COUNT + 1):
        with pytest.raises(ProviderTransientError):
            await advance(run, READER)
        units = await _units(run, pending)
        assert {u.attempt_count for u in units} == {attempt}
        expected = (
            FetchStatus.RETRY_EXHAUSTED
            if attempt == MAX_RETRY_COUNT
            else FetchStatus.FAILED_RETRYABLE
        )
        assert {u.fetch_status for u in units} == {expected}
    assert {u.state for u in await _units(run, pending)} == {UnitState.DELIVERED}


async def test_enospc_during_download_is_a_storage_error_with_no_attempt(
    run: TickRun, provider: FakeTickProvider, archive: Path
) -> None:
    pending, job = await _submitted(run, provider)
    provider.download_errors.append(OSError(errno.ENOSPC, "No space left on device"))
    with pytest.raises(TickArchiveWriteError) as raised:
        await advance(run, READER)
    assert str(archive / job.job_id) in str(raised.value)
    assert raised.value.errno == errno.ENOSPC
    assert {u.attempt_count for u in await _units(run, pending)} == {0}
    assert {u.state for u in await _units(run, pending)} == {UnitState.DELIVERED}


# -- manifest.json and verification -------------------------------------------


@pytest.mark.parametrize("supplied", [False, True])
async def test_manifest_json_is_written_only_when_the_job_lacked_one(
    run: TickRun, provider: FakeTickProvider, archive: Path, supplied: bool
) -> None:
    provider.supply_manifest = supplied
    pending, job = await _submitted(run, provider)
    await advance(run, READER)
    job_dir = archive / job.job_id
    manifest = json.loads((job_dir / "manifest.json").read_text())
    assert manifest["job_id"] == job.job_id
    listed = {entry["filename"] for entry in manifest["files"]}
    on_disk = {p.name for p in job_dir.iterdir()}
    assert listed == on_disk - {"manifest.json"}
    assert not any(name.endswith(".partial") for name in on_disk)
    assert any(name.endswith(".trades.dbn.zst") for name in listed)


async def test_a_provider_error_during_verify_leaves_units_downloaded_for_next_time(
    run: TickRun, provider: FakeTickProvider
) -> None:
    pending, _ = await _submitted(run, provider)
    provider.record_count_error = ProviderTransientError("count down")
    with pytest.raises(ProviderTransientError):
        await advance(run, READER)
    units = await _units(run, pending)
    assert {u.state for u in units} == {UnitState.DOWNLOADED}
    assert {u.fetch_status for u in units} == {FetchStatus.UNKNOWN}
    provider.record_count_error = None
    tally = await advance(run, READER)
    assert tally.verified == len(DAYS)
    assert {u.state for u in await _units(run, pending)} == {UnitState.VERIFIED}
    assert {u.provider_record_count for u in await _units(run, pending)} == {
        provider.records_per_day
    }


async def test_a_job_still_processing_is_reported_in_flight_with_its_deadline(
    run: TickRun, provider: FakeTickProvider
) -> None:
    provider.polls_until_done = None
    pending, job = await _submitted(run, provider)
    provider.jobs[job.job_id].state = BatchJobState.PROCESSING
    provider.jobs[job.job_id].polls_until_done = None
    tally = await advance(run, READER)
    assert [(j.job_id, j.state) for j in tally.in_flight] == [
        (job.job_id, "processing")
    ]
    assert tally.in_flight[0].deadline == job.ts_received + timedelta(days=30)
    assert {u.state for u in await _units(run, pending)} == {UnitState.SUBMITTED}
