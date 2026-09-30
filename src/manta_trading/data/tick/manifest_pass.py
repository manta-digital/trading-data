"""Manifest writes the acquisition pass adds (224); the seam from ``manifest_repo``.

``manifest_repo.py`` holds 223's per-unit compare-and-set primitives and
``manifest_reads.py`` the reads; this module holds 224's, split to keep every
file under ~300 lines. Same rule (LLD 224 TD2): each ``UPDATE`` names the
state it moves from in its ``WHERE``, and one that matches nothing raises
:class:`ManifestTransitionError`. The exception is ``reopen_holes_on_day``,
where matching no unit is normal.

The submit path is TD9: :func:`insert_pending_request` stamps the attempt
*before* the paid call; :func:`record_submit` follows an accepted one; every
outcome in between is :func:`mark_submit_outcome`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import DeliveryMode, UnitState
from manta_trading.data.tick.manifest_reads import OPEN_STATUS_VALUES, Conn
from manta_trading.data.tick.manifest_repo import (
    ADVANCE_SET,
    ManifestTransitionError,
    advance_params,
)
from manta_trading.data.tick.planner import PlannedRequest


@dataclass(frozen=True)
class PendingRequest:
    """A request written at *requested*: its id and one unit id per day."""

    request_id: int
    unit_ids: dict[date, int]


async def insert_pending_request(
    conn: Conn,
    planned: PlannedRequest,
    estimate: Decimal,
    now: datetime,
    repurchase_of: Mapping[date, int],
) -> PendingRequest:
    """One transaction: the request and its units at *requested*, before the
    paid call (TD9). ``attempt_count = 1`` and ``last_attempt_at = now`` are
    stamped now, so a crash after the provider accepts leaves the evidence.

    ``repurchase_of`` maps a day to the reopened unit it replaces; the pair of
    links (``repurchase_of_unit_id`` on the new unit, ``superseded_by_unit_id``
    on the old) is written in this same transaction (TD8).
    """
    request = planned.request
    async with conn.transaction():
        cursor = await conn.execute(
            "INSERT INTO tick_request (dataset, schema, symbols, stype_in,"
            " range_start, range_end, delivery_mode, is_adopted,"
            " estimated_cost_usd, requested_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s)"
            " RETURNING request_id",
            (
                request.dataset,
                request.schema.value,
                list(request.symbols),
                request.stype_in.value,
                request.start,
                request.end,
                DeliveryMode.BATCH_JOB.value,
                estimate,
                now,
            ),
        )
        row = await cursor.fetchone()
        assert row is not None  # RETURNING on a successful INSERT
        request_id = int(row[0])
        unit_ids = {
            day: await _insert_pending_unit(
                conn, request_id, day, now, repurchase_of.get(day)
            )
            for day in planned.days
        }
        for day, old_id in repurchase_of.items():
            await _supersede(conn, old_id, unit_ids[day])
    return PendingRequest(request_id, unit_ids)


async def _insert_pending_unit(
    conn: Conn, request_id: int, day: date, now: datetime, repurchase_of: int | None
) -> int:
    cursor = await conn.execute(
        "INSERT INTO tick_archive_unit (request_id, unit_date, state,"
        " state_changed_at, fetch_status, attempt_count, last_attempt_at,"
        " repurchase_of_unit_id) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)"
        " RETURNING unit_id",
        (
            request_id,
            day,
            UnitState.REQUESTED.value,
            now,
            FetchStatus.UNKNOWN.value,
            now,
            repurchase_of,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None  # RETURNING on a successful INSERT
    return int(row[0])


async def _supersede(conn: Conn, old_id: int, new_id: int) -> None:
    cursor = await conn.execute(
        "UPDATE tick_archive_unit SET superseded_by_unit_id = %s"
        " WHERE unit_id = %s AND reopened_at IS NOT NULL"
        " AND superseded_by_unit_id IS NULL",
        (new_id, old_id),
    )
    if cursor.rowcount != 1:
        raise ManifestTransitionError(old_id, "reopened and not superseded")


async def restamp_attempt(conn: Conn, request_id: int, now: datetime) -> None:
    """A re-submit after ``reset``: the same row is stamped again (TD9)."""
    async with conn.transaction():
        cursor = await conn.execute(
            "UPDATE tick_archive_unit SET attempt_count = 1, last_attempt_at = %s"
            " WHERE request_id = %s AND state = %s AND fetch_status = %s"
            " AND attempt_count = 0 AND reopened_at IS NULL",
            (
                now,
                request_id,
                UnitState.REQUESTED.value,
                FetchStatus.UNKNOWN.value,
            ),
        )
        if cursor.rowcount == 0:
            raise ManifestTransitionError(request_id, "requested with no attempts")


async def record_submit(
    conn: Conn, request_id: int, job_id: str, committed_at: datetime, now: datetime
) -> None:
    """The provider accepted the job: record its id and ``committed_at`` (the
    provider's clock) and move the request's *requested* units to *submitted*.
    Also the resolution of an unknown submit (reconcile found the job)."""
    async with conn.transaction():
        cursor = await conn.execute(
            "UPDATE tick_request SET provider_job_id = %s, committed_at = %s"
            " WHERE request_id = %s AND provider_job_id IS NULL",
            (job_id, committed_at, request_id),
        )
        if cursor.rowcount != 1:
            raise ManifestTransitionError(request_id, "a request with no job id")
        await _advance_requested(conn, request_id, UnitState.SUBMITTED, now)


async def _advance_requested(
    conn: Conn, request_id: int, to: UnitState, now: datetime
) -> None:
    cursor = await conn.execute(
        f"UPDATE tick_archive_unit SET {ADVANCE_SET}"
        " WHERE request_id = %s AND state = %s AND reopened_at IS NULL",
        (
            to.value,
            now,
            FetchStatus.UNKNOWN.value,
            request_id,
            UnitState.REQUESTED.value,
        ),
    )
    if cursor.rowcount == 0:
        raise ManifestTransitionError(request_id, "a request with units at requested")


async def mark_submit_outcome(
    conn: Conn, request_id: int, status: FetchStatus, reason: str
) -> None:
    """A jobless request's units at *requested* take ``status`` and ``reason``
    without counting an attempt (the pre-submit insert already did, TD9)."""
    async with conn.transaction():
        cursor = await conn.execute(
            "UPDATE tick_archive_unit SET fetch_status = %s, failure_reason = %s"
            " WHERE request_id = %s AND state = %s AND reopened_at IS NULL"
            " AND fetch_status = ANY(%s)",
            (
                status.value,
                reason,
                request_id,
                UnitState.REQUESTED.value,
                [FetchStatus.UNKNOWN.value, FetchStatus.FAILED_RETRYABLE.value],
            ),
        )
        if cursor.rowcount == 0:
            raise ManifestTransitionError(request_id, "requested and not exhausted")


async def mark_delivered(
    conn: Conn,
    request_id: int,
    *,
    cost: Decimal,
    record_count: int | None,
    billed_size: int | None,
    deadline: datetime | None,
    now: datetime,
) -> None:
    """The job is ``done``: its facts go on the request, its *submitted* units
    to *delivered* (one transaction)."""
    async with conn.transaction():
        await conn.execute(
            "UPDATE tick_request SET actual_cost_usd = %s,"
            " provider_record_count = %s, billed_size_bytes = %s,"
            " download_deadline = %s WHERE request_id = %s",
            (cost, record_count, billed_size, deadline, request_id),
        )
        cursor = await conn.execute(
            f"UPDATE tick_archive_unit SET {ADVANCE_SET}"
            " WHERE request_id = %s AND state = %s AND reopened_at IS NULL"
            " AND fetch_status = ANY(%s)",
            (
                *advance_params(UnitState.DELIVERED, now),
                request_id,
                UnitState.SUBMITTED.value,
                OPEN_STATUS_VALUES,
            ),
        )
        if cursor.rowcount == 0:
            raise ManifestTransitionError(request_id, "a request with submitted units")


async def expire_units(
    conn: Conn, unit_ids: list[int], reason: str, now: datetime
) -> int:
    """Units that can never produce their file: ``RETRY_EXHAUSTED`` with the
    reason, and ``reopened_at`` so the planner wants the day again (TD8).
    Only *submitted* or *delivered*, never a hole or a unit already reopened."""
    async with conn.transaction():
        cursor = await conn.execute(
            "UPDATE tick_archive_unit SET fetch_status = %s, failure_reason = %s,"
            " reopened_at = %s WHERE unit_id = ANY(%s) AND state = ANY(%s)"
            " AND fetch_status <> %s AND reopened_at IS NULL",
            (
                FetchStatus.RETRY_EXHAUSTED.value,
                reason,
                now,
                unit_ids,
                [UnitState.SUBMITTED.value, UnitState.DELIVERED.value],
                FetchStatus.PROVIDER_HOLE.value,
            ),
        )
        return cursor.rowcount


async def reopen_holes_on_day(
    conn: Conn, dataset: str, day: date, now: datetime
) -> int:
    """``PROVIDER_HOLE``, not reopened → ``reopened_at = now`` for one day (TD8).

    Every holed unit of the dataset's day, across requests; the caller's
    transaction holds it. Matching none is normal (the day had no hole).
    """
    cursor = await conn.execute(
        "UPDATE tick_archive_unit u SET reopened_at = %s FROM tick_request r"
        " WHERE r.request_id = u.request_id AND r.dataset = %s"
        " AND u.unit_date = %s AND u.fetch_status = %s AND u.reopened_at IS NULL",
        (now, dataset, day, FetchStatus.PROVIDER_HOLE.value),
    )
    return cursor.rowcount
