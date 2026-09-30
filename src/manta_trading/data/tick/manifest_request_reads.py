"""Request-level reads of the tick manifest: delivery and spend (224).

Split from ``manifest_reads.py`` (unit and day-key reads) to keep both under
~300 lines. Row types for units live there; ``RequestRow`` lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, LiteralString

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import SType, TickSchema, UnitState
from manta_trading.data.tick.manifest_reads import OPEN_STATUS_VALUES, Conn
from manta_trading.data.tick.provider import TickRequest
from manta_trading.data.tick.spend_guard import TrailingRow


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
        (state.value, OPEN_STATUS_VALUES),
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
