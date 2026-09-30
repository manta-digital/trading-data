"""Reads of the tick manifest and the row types they return (223).

The read half of the manifest repository (``manifest_repo.py`` holds the
writes), split along that seam to keep both under ~300 lines; the request-level reads
are in ``manifest_request_reads.py``. Every SQL statement on ``tick_request``
and ``tick_archive_unit`` is in one of the three.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
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

Conn = psycopg.AsyncConnection[Any]

#: ``fetch_status`` values a unit can still move from.
OPEN_STATUS_VALUES = [status.value for status in OPEN_FETCH_STATUSES]

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
        (state.value, OPEN_STATUS_VALUES),
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
        (UnitState.VERIFIED.value, TickSchema.DEFINITION.value, OPEN_STATUS_VALUES),
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
