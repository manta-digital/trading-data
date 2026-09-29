"""Reads of the tick manifest and the row types they return (223).

The read half of the manifest repository (``manifest_repo.py`` holds the
writes), split along that seam to keep both under ~300 lines. Every SQL
statement on ``tick_request`` and ``tick_archive_unit`` is in one of the two.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, LiteralString

import psycopg
from psycopg.rows import dict_row

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import SType, TickSchema, UnitState

Conn = psycopg.AsyncConnection[Any]

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
    " r.dataset, r.schema, r.symbols, r.stype_in"
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
    )


async def _unit_rows(
    conn: Conn, where: LiteralString, params: tuple[object, ...]
) -> list[UnitRow]:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(f"{_UNIT_SELECT} WHERE {where} ORDER BY u.unit_id", params)
        return [_unit_row(row) for row in await cursor.fetchall()]


async def units_by_id(conn: Conn, unit_ids: Iterable[int]) -> list[UnitRow]:
    return await _unit_rows(conn, "u.unit_id = ANY(%s)", (list(unit_ids),))


async def units_of_request(conn: Conn, request_id: int) -> list[UnitRow]:
    return await _unit_rows(conn, "u.request_id = %s", (request_id,))


async def all_unit_ids(conn: Conn) -> list[int]:
    cursor = await conn.execute("SELECT unit_id FROM tick_archive_unit ORDER BY 1")
    return [int(row[0]) for row in await cursor.fetchall()]
