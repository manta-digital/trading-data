"""The three ``mt data tick debug`` reads: requests, units, definitions (230).

LLD 230 TD6. Plain reads of the manifest and the contract definitions, so an
investigation needs no ad-hoc SQL. Each returns every row its filters select
(no cap: the whole 2020+ manifest is a few thousand rows), as dicts keyed by
column, with raw values: ints, dates, timestamps, the unit enums. Formatting
is the renderer's. A filter left ``None`` does not filter. No CLI import.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import OUTRIGHT_CLASS, UnitState
from manta_trading.data.tick.manifest_reads import Conn

Row = dict[str, Any]

#: Columns holding epoch nanoseconds and fixed-point prices, for the renderer.
NS_COLUMNS = frozenset({"activation_ns", "expiration_ns"})
PRICE_COLUMNS = frozenset({"min_price_increment"})

#: A request meets ``[start, end)`` when its own ``[range_start, range_end)``
#: overlaps it.
_REQUESTS = (
    "SELECT request_id, schema, symbols, stype_in, range_start, range_end,"
    " delivery_mode, is_adopted, provider_job_id, estimated_cost_usd,"
    " actual_cost_usd, provider_record_count, billed_size_bytes, requested_at,"
    " committed_at, download_deadline FROM tick_request"
    " WHERE (%(symbols)s::text[] IS NULL OR symbols = %(symbols)s)"
    " AND (%(end)s::date IS NULL OR range_start < %(end)s)"
    " AND (%(start)s::date IS NULL OR range_end > %(start)s)"
    " ORDER BY request_id"
)
_UNITS = (
    "SELECT u.unit_id, u.request_id, u.unit_date, r.schema, u.state,"
    " u.fetch_status, u.failure_reason, u.attempt_count, u.last_attempt_at,"
    " u.file_path, u.file_size_bytes, u.provider_record_count,"
    " u.decoded_record_count, u.superseded_by_unit_id, u.repurchase_of_unit_id,"
    " u.reopened_at FROM tick_archive_unit u JOIN tick_request r USING (request_id)"
    " WHERE (%(symbols)s::text[] IS NULL OR r.symbols = %(symbols)s)"
    " AND (%(start)s::date IS NULL OR u.unit_date >= %(start)s)"
    " AND (%(end)s::date IS NULL OR u.unit_date < %(end)s)"
    " AND (%(state)s::text IS NULL OR u.state = %(state)s)"
    " AND (%(fetch_status)s::text IS NULL OR u.fetch_status = %(fetch_status)s)"
    " ORDER BY u.unit_date, u.unit_id"
)
_DEFINITIONS = (
    "SELECT instrument_id, raw_symbol, instrument_class, activation_ns,"
    " expiration_ns, min_price_increment, contract_multiplier, currency, unit_id"
    " FROM tick_definition"
    " WHERE (%(asset)s::text IS NULL OR asset = %(asset)s)"
    " AND (%(symbol)s::text IS NULL OR raw_symbol = %(symbol)s)"
    " AND (%(at_ns)s::bigint IS NULL"
    " OR (activation_ns <= %(at_ns)s AND %(at_ns)s <= expiration_ns))"
    " AND (%(spreads)s OR instrument_class = %(outright)s)"
    " ORDER BY expiration_ns, instrument_id, activation_ns"
)


async def _rows(conn: Conn, query: str, params: dict[str, Any]) -> list[Row]:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, params)  # type: ignore[arg-type]
        return await cursor.fetchall()


async def requests(
    conn: Conn,
    symbols: Sequence[str] | None,
    start: date | None,
    end: date | None,
) -> list[Row]:
    """``tick_request`` rows of the shape ``symbols``, meeting ``[start, end)``."""
    params = {
        "symbols": None if symbols is None else list(symbols),
        "start": start,
        "end": end,
    }
    return await _rows(conn, _REQUESTS, params)


async def units(
    conn: Conn,
    symbols: Sequence[str] | None,
    span: tuple[date | None, date | None],
    state: UnitState | None,
    fetch_status: FetchStatus | None,
) -> list[Row]:
    """``tick_archive_unit`` rows with their request's schema; ``unit_date``
    in ``span`` (end exclusive)."""
    params = {
        "symbols": None if symbols is None else list(symbols),
        "start": span[0],
        "end": span[1],
        "state": None if state is None else state.value,
        "fetch_status": None if fetch_status is None else fetch_status.value,
    }
    rows = await _rows(conn, _UNITS, params)
    for row in rows:
        row["state"] = UnitState(row["state"])
        row["fetch_status"] = FetchStatus(row["fetch_status"])
    return rows


async def definitions(
    conn: Conn,
    asset: str | None,
    symbol: str | None,
    at_ns: int | None,
    spreads: bool,
) -> list[Row]:
    """``tick_definition`` rows; outrights only unless ``spreads``. ``at_ns``
    keeps the rows valid at that instant (a session's open, resolved by the
    caller from the calendar)."""
    params = {
        "asset": asset,
        "symbol": symbol,
        "at_ns": at_ns,
        "spreads": spreads,
        "outright": OUTRIGHT_CLASS,
    }
    return await _rows(conn, _DEFINITIONS, params)
