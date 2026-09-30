"""The acquisition pass's manifest transitions on a migrated tick database (224).

LLD 224 TD2 (compare-and-set), TD8 (reopen, supersession) and TD9 (the submit
path). Requests and units are set up with ``tick_support.rows`` or the
functions under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from tick_support.fake_provider import request_for
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit
from tick_support.runs import connect

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    CME_DATASET,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.manifest_pass import (
    expire_units,
    insert_pending_request,
    mark_delivered,
    mark_submit_outcome,
    record_submit,
    reopen_holes_on_day,
    restamp_attempt,
)
from manta_trading.data.tick.manifest_reads import (
    covered_keys,
    jobless_requests,
    reopened_unit_ids,
    requests_with_units_in,
    swept_candidates,
    trailing_spend_rows,
    units_of_request,
)
from manta_trading.data.tick.manifest_repo import (
    ManifestTransitionError,
    reset_exhausted,
)
from manta_trading.data.tick.planner import DayKey, PlannedRequest

AConn = psycopg.AsyncConnection[Any]
NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
DAYS = (date(2024, 9, 3), date(2024, 9, 4))
COST = Decimal("0.002")


def _planned(schema: TickSchema = TickSchema.DEFINITION) -> PlannedRequest:
    request = request_for(schema, DAYS[0], DAYS[-1] + timedelta(days=1))
    return PlannedRequest("ES", request, DAYS)


@pytest.fixture
async def conn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with connect(migrated_tick_db) as connection:
        yield connection


async def _unit(conn: AConn, unit_id: int) -> dict[str, Any]:
    cursor = await conn.execute(
        "SELECT state, fetch_status, attempt_count, last_attempt_at, reopened_at,"
        " superseded_by_unit_id, repurchase_of_unit_id, failure_reason"
        " FROM tick_archive_unit WHERE unit_id = %s",
        (unit_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    keys = (
        "state",
        "fetch_status",
        "attempt_count",
        "last_attempt_at",
        "reopened_at",
        "superseded_by",
        "repurchase_of",
        "reason",
    )
    return dict(zip(keys, row, strict=True))


async def test_pending_insert_stamps_the_attempt_before_the_paid_call(
    conn: AConn,
) -> None:
    pending = await insert_pending_request(conn, _planned(), COST, NOW, {})
    assert sorted(pending.unit_ids) == list(DAYS)
    for unit_id in pending.unit_ids.values():
        unit = await _unit(conn, unit_id)
        assert unit["state"] == UnitState.REQUESTED.value
        assert unit["fetch_status"] == FetchStatus.UNKNOWN.value
        assert (unit["attempt_count"], unit["last_attempt_at"]) == (1, NOW)
    (row,) = await jobless_requests(conn)
    assert row.request_id == pending.request_id
    assert (row.job_id, row.requested_at, row.estimated_cost_usd) == (None, NOW, COST)
    assert row.request == _planned().request


async def test_a_repurchase_writes_both_supersession_links_in_one_transaction(
    conn: AConn, migrated_tick_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as sync:
        old_request = insert_request(sync, schema=TickSchema.DEFINITION.value)
        old = insert_unit(
            sync,
            old_request,
            unit_date=DAYS[0],
            state=UnitState.SUBMITTED.value,
            fetch_status=FetchStatus.RETRY_EXHAUSTED.value,
            failure_reason="retention expired",
            reopened_at=NOW,
        )
    key = DayKey(CME_DATASET, TickSchema.DEFINITION, ("ES.FUT",), SType.PARENT, DAYS[0])
    assert await reopened_unit_ids(conn, key, list(DAYS)) == {DAYS[0]: old}
    pending = await insert_pending_request(conn, _planned(), COST, NOW, {DAYS[0]: old})
    new = pending.unit_ids[DAYS[0]]
    assert (await _unit(conn, new))["repurchase_of"] == old
    assert (await _unit(conn, old))["superseded_by"] == new
    assert await reopened_unit_ids(conn, key, list(DAYS)) == {}
    covered = await covered_keys(conn)
    assert key in covered


async def test_a_repurchase_of_a_unit_not_reopened_writes_nothing(
    conn: AConn, migrated_tick_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as sync:
        request = insert_request(sync, schema=TickSchema.DEFINITION.value)
        live = insert_unit(sync, request, unit_date=DAYS[0])
    with pytest.raises(ManifestTransitionError):
        await insert_pending_request(conn, _planned(), COST, NOW, {DAYS[0]: live})
    cursor = await conn.execute("SELECT count(*) FROM tick_request")
    assert await cursor.fetchone() == (1,)  # only the seeded request: rolled back


async def test_record_submit_moves_units_and_is_compare_and_set(conn: AConn) -> None:
    pending = await insert_pending_request(conn, _planned(), COST, NOW, {})
    committed = NOW + timedelta(seconds=3)
    await record_submit(conn, pending.request_id, "GLBX-1", committed, NOW)
    for unit_id in pending.unit_ids.values():
        unit = await _unit(conn, unit_id)
        assert unit["state"] == UnitState.SUBMITTED.value
        assert (unit["fetch_status"], unit["attempt_count"]) == ("UNKNOWN", 0)
    assert await jobless_requests(conn) == []
    (row,) = await requests_with_units_in(conn, UnitState.SUBMITTED)
    assert row.job_id == "GLBX-1"
    with pytest.raises(ManifestTransitionError):
        await record_submit(conn, pending.request_id, "GLBX-2", committed, NOW)


async def test_record_submit_also_resolves_an_exhausted_unknown(conn: AConn) -> None:
    pending = await insert_pending_request(conn, _planned(), COST, NOW, {})
    await mark_submit_outcome(
        conn, pending.request_id, FetchStatus.RETRY_EXHAUSTED, "unknown"
    )
    await record_submit(conn, pending.request_id, "GLBX-9", NOW, NOW)
    unit = await _unit(conn, pending.unit_ids[DAYS[0]])
    assert (unit["state"], unit["fetch_status"]) == ("submitted", "UNKNOWN")


async def test_mark_submit_outcome_keeps_the_attempt(conn: AConn) -> None:
    pending = await insert_pending_request(conn, _planned(), COST, NOW, {})
    await mark_submit_outcome(
        conn, pending.request_id, FetchStatus.FAILED_RETRYABLE, "submit outcome unknown"
    )
    unit = await _unit(conn, pending.unit_ids[DAYS[0]])
    assert unit["fetch_status"] == FetchStatus.FAILED_RETRYABLE.value
    assert (unit["attempt_count"], unit["last_attempt_at"]) == (1, NOW)
    assert unit["reason"] == "submit outcome unknown"
    await record_submit(conn, pending.request_id, "GLBX-1", NOW, NOW)
    with pytest.raises(ManifestTransitionError):  # no longer at requested
        await mark_submit_outcome(
            conn, pending.request_id, FetchStatus.RETRY_EXHAUSTED, "x"
        )


async def test_restamp_after_reset_is_a_second_attempt_on_the_same_row(
    conn: AConn,
) -> None:
    pending = await insert_pending_request(conn, _planned(), COST, NOW, {})
    with pytest.raises(ManifestTransitionError):  # attempt_count is 1, not 0
        await restamp_attempt(conn, pending.request_id, NOW)
    await mark_submit_outcome(
        conn, pending.request_id, FetchStatus.RETRY_EXHAUSTED, "exhausted"
    )
    for unit_id in pending.unit_ids.values():
        await reset_exhausted(conn, unit_id)
        assert (await _unit(conn, unit_id))["attempt_count"] == 0
    later = NOW + timedelta(hours=2)
    await restamp_attempt(conn, pending.request_id, later)
    unit = await _unit(conn, pending.unit_ids[DAYS[0]])
    assert (unit["attempt_count"], unit["last_attempt_at"]) == (1, later)
    assert len(await jobless_requests(conn)) == 1  # the same row


async def test_mark_delivered_records_the_job_and_advances(conn: AConn) -> None:
    pending = await insert_pending_request(conn, _planned(), COST, NOW, {})
    await record_submit(conn, pending.request_id, "GLBX-1", NOW, NOW)
    deadline = NOW + timedelta(days=30)
    await mark_delivered(
        conn,
        pending.request_id,
        cost=Decimal("0.0021"),
        record_count=6,
        billed_size=2000,
        deadline=deadline,
        now=NOW,
    )
    cursor = await conn.execute(
        "SELECT actual_cost_usd, provider_record_count, billed_size_bytes,"
        " download_deadline FROM tick_request WHERE request_id = %s",
        (pending.request_id,),
    )
    assert await cursor.fetchone() == (Decimal("0.0021"), 6, 2000, deadline)
    units = await units_of_request(conn, pending.request_id)
    assert {u.state for u in units} == {UnitState.DELIVERED}
    with pytest.raises(ManifestTransitionError):  # nothing left at submitted
        await mark_delivered(
            conn,
            pending.request_id,
            cost=COST,
            record_count=None,
            billed_size=None,
            deadline=None,
            now=NOW,
        )


async def test_requests_with_units_are_ordered_by_deadline(conn: AConn) -> None:
    first = await insert_pending_request(conn, _planned(), COST, NOW, {})
    second_planned = PlannedRequest(
        "ES",
        request_for(TickSchema.DEFINITION, date(2024, 9, 10), date(2024, 9, 11)),
        (date(2024, 9, 10),),
    )
    second = await insert_pending_request(conn, second_planned, COST, NOW, {})
    for pending, job, deadline in (
        (first, "GLBX-LATE", NOW + timedelta(days=10)),
        (second, "GLBX-EARLY", NOW + timedelta(days=2)),
    ):
        await record_submit(conn, pending.request_id, job, NOW, NOW)
        await mark_delivered(
            conn,
            pending.request_id,
            cost=COST,
            record_count=None,
            billed_size=None,
            deadline=deadline,
            now=NOW,
        )
    rows = await requests_with_units_in(conn, UnitState.DELIVERED)
    assert [r.job_id for r in rows] == ["GLBX-EARLY", "GLBX-LATE"]


async def test_expiry_sweep_excludes_holes_and_the_future(
    conn: AConn, migrated_tick_db: str
) -> None:
    past, future = NOW - timedelta(days=1), NOW + timedelta(days=1)
    with psycopg.connect(migrated_tick_db, autocommit=True) as sync:
        expired = insert_request(sync, download_deadline=past, provider_job_id="A")
        live = insert_request(sync, download_deadline=future, provider_job_id="B")
        delivered = insert_unit(
            sync, expired, state=UnitState.DELIVERED.value, unit_date=DAYS[0]
        )
        hole = insert_unit(
            sync,
            expired,
            state=UnitState.DELIVERED.value,
            unit_date=DAYS[1],
            fetch_status=FetchStatus.PROVIDER_HOLE.value,
            failure_reason="hole",
        )
        alive = insert_unit(sync, live, state=UnitState.DELIVERED.value)
        downloaded = insert_unit(
            sync,
            expired,
            state=UnitState.DOWNLOADED.value,
            unit_date=date(2024, 9, 5),
            **FILE_COLUMNS,
        )
    swept = await swept_candidates(conn, NOW)
    assert swept == {past: [delivered]}
    assert {hole, alive, downloaded}.isdisjoint(swept[past])
    assert await expire_units(conn, [delivered, hole, downloaded], "gone", NOW) == 1
    unit = await _unit(conn, delivered)
    assert unit["fetch_status"] == FetchStatus.RETRY_EXHAUSTED.value
    assert (unit["reopened_at"], unit["reason"]) == (NOW, "gone")
    assert (await _unit(conn, hole))["reopened_at"] is None
    assert await swept_candidates(conn, NOW) == {}  # already reopened


async def test_reopen_holes_on_day_touches_only_that_days_holes(
    conn: AConn, migrated_tick_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as sync:
        request = insert_request(sync)
        holes = {
            day: insert_unit(
                sync,
                request,
                unit_date=day,
                state=UnitState.DELIVERED.value,
                fetch_status=FetchStatus.PROVIDER_HOLE.value,
                failure_reason="hole",
            )
            for day in DAYS
        }
    assert await reopen_holes_on_day(conn, CME_DATASET, DAYS[0], NOW) == 1
    assert await reopen_holes_on_day(conn, CME_DATASET, DAYS[0], NOW) == 0
    assert (await _unit(conn, holes[DAYS[0]]))["reopened_at"] == NOW
    assert (await _unit(conn, holes[DAYS[1]]))["reopened_at"] is None


async def test_trailing_spend_rows_include_adopted_and_unaccepted(
    conn: AConn, migrated_tick_db: str
) -> None:
    old = NOW - timedelta(days=31)
    with psycopg.connect(migrated_tick_db, autocommit=True) as sync:
        insert_request(  # adopted: actual cost, committed_at
            sync,
            is_adopted=True,
            provider_job_id="ADOPTED",
            actual_cost_usd=Decimal("2"),
            committed_at=NOW - timedelta(days=1),
            requested_at=NOW - timedelta(days=1, hours=1),
        )
        insert_request(  # never accepted: estimate, requested_at
            sync,
            estimated_cost_usd=Decimal("0.5"),
            requested_at=NOW - timedelta(days=2),
        )
        insert_request(sync, estimated_cost_usd=Decimal("9"), requested_at=old)
    rows = await trailing_spend_rows(conn, NOW - timedelta(days=30))
    assert sorted((r.cost, r.job_id) for r in rows) == [
        (Decimal("0.5"), None),
        (Decimal("2"), "ADOPTED"),
    ]
