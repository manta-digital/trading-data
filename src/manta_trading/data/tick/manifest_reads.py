"""Reads of the tick manifest and the row types they return (223).

The read half of the manifest repository (``manifest_repo.py`` holds the
writes), split along that seam to keep both under ~300 lines. Every SQL
statement on ``tick_request`` and ``tick_archive_unit`` is in one of the two.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, LiteralString

import psycopg
from psycopg.rows import dict_row

from manta_trading.data.quality.fetch_status import OPEN_FETCH_STATUSES, FetchStatus
from manta_trading.data.tick.constants import (
    STORED_TIERS,
    UNIT_STATES_WITH_FILE,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.planner import DayKey
from manta_trading.data.tick.provider import TickRequest
from manta_trading.data.tick.spend_guard import TrailingRow

Conn = psycopg.AsyncConnection[Any]

_OPEN_STATUSES = [status.value for status in OPEN_FETCH_STATUSES]

#: A unit that counts as holding its day (LLD 224, State Management). The one
#: spelling of the coverage predicate; every "current unit" read uses it.
COVERAGE_PREDICATE: LiteralString = (
    "superseded_by_unit_id IS NULL AND reopened_at IS NULL"
)


@dataclass(frozen=True)
class UnitFile:
    """A unit's archived file; ``path`` is relative to the archive root (TD11)."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UnitRow:
    """A unit joined with the request facts verification needs."""

    unit_id: int
    request_id: int
    unit_date: date
    state: UnitState
    fetch_status: FetchStatus
    failure_reason: str | None
    attempt_count: int
    reopened_at: datetime | None
    file: UnitFile | None
    provider_record_count: int | None
    dataset: str
    schema: TickSchema
    symbols: tuple[str, ...]
    stype_in: SType
    last_attempt_at: datetime | None = None


# -- reads ------------------------------------------------------------------------


async def request_id_for_job(conn: Conn, job_id: str) -> int | None:
    cursor = await conn.execute(
        "SELECT request_id FROM tick_request WHERE provider_job_id = %s", (job_id,)
    )
    row = await cursor.fetchone()
    return None if row is None else int(row[0])


_UNIT_SELECT: LiteralString = (
    "SELECT u.unit_id, u.request_id, u.unit_date, u.state, u.fetch_status,"
    " u.failure_reason, u.attempt_count, u.reopened_at, u.file_path,"
    " u.file_size_bytes, u.file_sha256, u.provider_record_count,"
    " r.dataset, r.schema, r.symbols, r.stype_in, u.last_attempt_at"
    " FROM tick_archive_unit u JOIN tick_request r USING (request_id)"
)


def _unit_row(row: dict[str, Any]) -> UnitRow:
    has_file = row["file_path"] is not None
    return UnitRow(
        unit_id=row["unit_id"],
        request_id=row["request_id"],
        unit_date=row["unit_date"],
        state=UnitState(row["state"]),
        fetch_status=FetchStatus(row["fetch_status"]),
        failure_reason=row["failure_reason"],
        attempt_count=row["attempt_count"],
        reopened_at=row["reopened_at"],
        file=UnitFile(row["file_path"], row["file_size_bytes"], row["file_sha256"])
        if has_file
        else None,
        provider_record_count=row["provider_record_count"],
        dataset=row["dataset"],
        schema=TickSchema(row["schema"]),
        symbols=tuple(row["symbols"]),
        stype_in=SType(row["stype_in"]),
        last_attempt_at=row["last_attempt_at"],
    )


async def _unit_rows(
    conn: Conn,
    where: LiteralString,
    params: tuple[object, ...],
    order: LiteralString = "u.unit_id",
) -> list[UnitRow]:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(f"{_UNIT_SELECT} WHERE {where} ORDER BY {order}", params)
        return [_unit_row(row) for row in await cursor.fetchall()]


async def units_by_id(conn: Conn, unit_ids: Iterable[int]) -> list[UnitRow]:
    return await _unit_rows(conn, "u.unit_id = ANY(%s)", (list(unit_ids),))


async def open_units_in(conn: Conn, state: UnitState) -> list[UnitRow]:
    """Units at ``state`` whose failure lifecycle is open and not reopened."""
    return await _unit_rows(
        conn,
        "u.state = %s AND u.reopened_at IS NULL AND u.fetch_status = ANY(%s)",
        (state.value, _OPEN_STATUSES),
    )


async def verified_definition_units(conn: Conn) -> list[UnitRow]:
    """Definition units at *verified*, earliest day first (TD5).

    The only schema the acquisition pass projects: tier units always go through
    225's ingest, so this selects on ``TickSchema.DEFINITION`` and nothing else.
    """
    return await _unit_rows(
        conn,
        "u.state = %s AND r.schema = %s AND u.reopened_at IS NULL"
        " AND u.fetch_status = ANY(%s)",
        (UnitState.VERIFIED.value, TickSchema.DEFINITION.value, _OPEN_STATUSES),
        order="u.unit_date, u.unit_id",
    )


async def units_of_request(conn: Conn, request_id: int) -> list[UnitRow]:
    return await _unit_rows(conn, "u.request_id = %s", (request_id,))


async def all_unit_ids(conn: Conn) -> list[int]:
    cursor = await conn.execute("SELECT unit_id FROM tick_archive_unit ORDER BY 1")
    return [int(row[0]) for row in await cursor.fetchall()]


# -- reads for the acquisition pass (224) -----------------------------------------

_DAY_KEY_SELECT: LiteralString = (
    "SELECT r.dataset, r.schema, r.symbols, r.stype_in, u.unit_date"
    " FROM tick_archive_unit u JOIN tick_request r USING (request_id)"
)


def _day_key(row: tuple[Any, ...]) -> DayKey:
    dataset, schema, symbols, stype_in, day = row
    return DayKey(dataset, TickSchema(schema), tuple(symbols), SType(stype_in), day)


async def covered_keys(conn: Conn) -> frozenset[DayKey]:
    """Every ``(dataset, schema, symbols, stype_in, day)`` a current unit holds."""
    cursor = await conn.execute(f"{_DAY_KEY_SELECT} WHERE {COVERAGE_PREDICATE}")
    return frozenset(_day_key(row) for row in await cursor.fetchall())


async def owned_tier_days(conn: Conn) -> list[DayKey]:
    """Tier days the manifest holds a file for (the source of companion wants)."""
    cursor = await conn.execute(
        f"{_DAY_KEY_SELECT} WHERE r.schema = ANY(%s) AND u.state = ANY(%s)"
        f" AND {COVERAGE_PREDICATE}",
        (
            sorted(tier.value for tier in STORED_TIERS),
            sorted(state.value for state in UNIT_STATES_WITH_FILE),
        ),
    )
    return [_day_key(row) for row in await cursor.fetchall()]


async def holed_days(conn: Conn) -> list[tuple[str, date]]:
    """``(dataset, day)`` of every hole not yet reopened."""
    cursor = await conn.execute(
        "SELECT DISTINCT r.dataset, u.unit_date"
        " FROM tick_archive_unit u JOIN tick_request r USING (request_id)"
        " WHERE u.fetch_status = %s AND u.reopened_at IS NULL"
        " ORDER BY 1, 2",
        (FetchStatus.PROVIDER_HOLE.value,),
    )
    return [(row[0], row[1]) for row in await cursor.fetchall()]


# -- request-level reads (224 delivery and spend) -----------------------------------


@dataclass(frozen=True)
class RequestRow:
    """A ``tick_request`` with the provider request it stands for."""

    request_id: int
    request: TickRequest
    job_id: str | None
    requested_at: datetime
    estimated_cost_usd: Decimal
    download_deadline: datetime | None


_REQUEST_SELECT: LiteralString = (
    "SELECT r.request_id, r.dataset, r.schema, r.symbols, r.stype_in,"
    " r.range_start, r.range_end, r.provider_job_id, r.requested_at,"
    " r.estimated_cost_usd, r.download_deadline FROM tick_request r"
)


def _request_row(row: tuple[Any, ...]) -> RequestRow:
    request_id, dataset, schema, symbols, stype_in, start, end = row[:7]
    return RequestRow(
        request_id=request_id,
        request=TickRequest(
            dataset, tuple(symbols), SType(stype_in), TickSchema(schema), start, end
        ),
        job_id=row[7],
        requested_at=row[8],
        estimated_cost_usd=row[9],
        download_deadline=row[10],
    )


async def _requests(
    conn: Conn, where: LiteralString, params: tuple[object, ...] = ()
) -> list[RequestRow]:
    cursor = await conn.execute(
        f"{_REQUEST_SELECT} WHERE {where}"
        " ORDER BY r.download_deadline NULLS LAST, r.request_id",
        params,
    )
    return [_request_row(row) for row in await cursor.fetchall()]


async def jobless_requests(conn: Conn) -> list[RequestRow]:
    """Requests with no job id that still have a unit at *requested* (TD9)."""
    return await _requests(
        conn,
        "r.provider_job_id IS NULL AND EXISTS (SELECT 1 FROM tick_archive_unit u"
        " WHERE u.request_id = r.request_id AND u.state = %s"
        " AND u.reopened_at IS NULL)",
        (UnitState.REQUESTED.value,),
    )


async def resubmittable_requests(conn: Conn) -> list[RequestRow]:
    """Jobless requests whose units are back at *requested* with no attempts:
    what ``reset`` produces, and the only jobless state the purchase phase
    submits (TD9)."""
    return await _requests(
        conn,
        "r.provider_job_id IS NULL AND EXISTS (SELECT 1 FROM tick_archive_unit u"
        " WHERE u.request_id = r.request_id AND u.state = %s AND u.fetch_status = %s"
        " AND u.attempt_count = 0 AND u.reopened_at IS NULL)",
        (UnitState.REQUESTED.value, FetchStatus.UNKNOWN.value),
    )


async def requests_with_units_in(conn: Conn, state: UnitState) -> list[RequestRow]:
    """Requests holding a job id and an open unit in ``state``, earliest
    download deadline first (the order downloads run in, TD9)."""
    return await _requests(
        conn,
        "r.provider_job_id IS NOT NULL AND EXISTS (SELECT 1 FROM"
        " tick_archive_unit u WHERE u.request_id = r.request_id AND u.state = %s"
        " AND u.reopened_at IS NULL AND u.fetch_status = ANY(%s))",
        (state.value, _OPEN_STATUSES),
    )


async def swept_candidates(conn: Conn, now: datetime) -> dict[datetime, list[int]]:
    """Units at *submitted* or *delivered* whose request deadline has passed
    and that are not holes (the expiry sweep, TD8): unit ids by deadline."""
    cursor = await conn.execute(
        "SELECT r.download_deadline, u.unit_id FROM tick_archive_unit u"
        " JOIN tick_request r USING (request_id) WHERE u.state = ANY(%s)"
        " AND u.fetch_status <> %s AND u.reopened_at IS NULL"
        " AND r.download_deadline < %s ORDER BY 1, 2",
        (
            [UnitState.SUBMITTED.value, UnitState.DELIVERED.value],
            FetchStatus.PROVIDER_HOLE.value,
            now,
        ),
    )
    by_deadline: dict[datetime, list[int]] = {}
    for deadline, unit_id in await cursor.fetchall():
        by_deadline.setdefault(deadline, []).append(int(unit_id))
    return by_deadline


async def reopened_unit_ids(
    conn: Conn, request: TickRequest, days: list[date]
) -> dict[date, int]:
    """Reopened, not-yet-superseded units of ``request``'s shape (dataset,
    schema, symbols, ``stype_in``), by day: what a repurchase supersedes (TD8)."""
    cursor = await conn.execute(
        "SELECT u.unit_date, u.unit_id FROM tick_archive_unit u"
        " JOIN tick_request r USING (request_id) WHERE r.dataset = %s"
        " AND r.schema = %s AND r.symbols = %s AND r.stype_in = %s"
        " AND u.unit_date = ANY(%s) AND u.reopened_at IS NOT NULL"
        " AND u.superseded_by_unit_id IS NULL",
        (
            request.dataset,
            request.schema.value,
            list(request.symbols),
            request.stype_in.value,
            days,
        ),
    )
    return {row[0]: int(row[1]) for row in await cursor.fetchall()}


async def trailing_spend_rows(conn: Conn, since: datetime) -> list[TrailingRow]:
    """Every request row inside the spend window, adopted and unaccepted included
    (TD7): ``COALESCE(actual, estimated)`` at ``COALESCE(committed, requested)``."""
    cursor = await conn.execute(
        "SELECT COALESCE(committed_at, requested_at),"
        " COALESCE(actual_cost_usd, estimated_cost_usd), provider_job_id"
        " FROM tick_request WHERE COALESCE(committed_at, requested_at) >= %s",
        (since,),
    )
    return [TrailingRow(row[0], row[1], row[2]) for row in await cursor.fetchall()]
