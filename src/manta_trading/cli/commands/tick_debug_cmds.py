"""``mt data tick debug {requests, units, definitions}`` (slice 230, TD6).

Plain reads of the manifest and the contract definitions: a Rich table per
verb, or ``{rows, exit_code}`` with ``--json``. They print facts and assert
nothing, so they exit 0 (no rows included), 1 or 4, never 3. Provider-free:
the tick track's migrations are checked first (``connect_migrated``), then
one ``READ ONLY`` transaction. Registered on ``tick_app`` in ``tick.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import typer
from rich.text import Text

from manta_trading.cli.commands.tick_active_cmds import refuse
from manta_trading.cli.commands.tick_exit import (
    EXIT_OK,
    EXIT_PREFLIGHT,
    EXIT_STORAGE,
)
from manta_trading.cli.commands.tick_store_cmds import (
    DATE_FORMAT,
    JSON_OPTION,
    PRODUCT_OPTION,
    read_storage,
    run_mapped,
    universe_entries,
)
from manta_trading.cli.output import make_table, print_result
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import UnitState
from manta_trading.data.tick.debug_reads import NS_COLUMNS, PRICE_COLUMNS, DebugRow
from manta_trading.data.tick.tick_format import price_text, ts_text

if TYPE_CHECKING:
    from manta_trading.config import Settings
    from manta_trading.data.tick.manifest_reads import Conn

DEBUG_EPILOG = (
    f"Exit codes: {EXIT_OK} ok, including no rows; {EXIT_PREFLIGHT} a bad option "
    "value, an unknown --product, a pending tick migration, or MT_TICK_DB_URL "
    f"unset; {EXIT_STORAGE} the tick or calendar database is unreachable. "
    "Narrow to one unit or request with --json | jq."
)
debug_app = typer.Typer(
    name="debug",
    help="Plain reads of the tick manifest and definitions, for investigation.",
    no_args_is_help=True,
)
_START_OPTION = typer.Option(
    None, "--start", formats=[DATE_FORMAT], help="First date (inclusive)."
)
_END_OPTION = typer.Option(
    None, "--end", formats=[DATE_FORMAT], help="End date, EXCLUSIVE."
)
_STATE_OPTION = typer.Option(
    None, "--state", help=f"Unit state: {', '.join(UnitState)}."
)
_FETCH_STATUS_OPTION = typer.Option(
    None, "--fetch-status", help=f"Fetch status: {', '.join(FetchStatus)}."
)
_SYMBOL_OPTION = typer.Option(None, "--symbol", help="One raw symbol, e.g. ESZ4.")
_AS_OF_OPTION = typer.Option(
    None,
    "--as-of",
    formats=[DATE_FORMAT],
    help="Only definitions valid at this session's open (needs --product).",
)
_SPREADS_OPTION = typer.Option(
    False, "--spreads", help="Include spreads (outrights only by default)."
)


def _member[E: StrEnum](
    kind: type[E], value: str | None, option: str, json: bool
) -> E | None:
    if value is None:
        return None
    try:
        return kind(value)
    except ValueError:
        refuse(f"{option} {value!r} is not one of {', '.join(kind)}", json)


def _day(value: datetime | None) -> date | None:
    return None if value is None else value.date()


def _cell(name: str, value: Any) -> Any:
    """One JSON-ready value: exact prices, nanosecond times, ISO dates."""
    if value is None:
        return None
    if name in PRICE_COLUMNS:
        return price_text(value)
    if name in NS_COLUMNS:
        return ts_text(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal | StrEnum):
        return str(value)
    if isinstance(value, list):
        return ",".join(map(str, value))
    return value


def _print(title: str, rows: list[DebugRow], json_output: bool) -> None:
    cells = [{k: _cell(k, v) for k, v in row.items()} for row in rows]
    if json_output:
        print_result({"rows": cells, "exit_code": EXIT_OK}, json_mode=True)
        return
    columns = list(cells[0]) if cells else []
    table = make_table(f"{title}: {len(cells)} row(s)", [(c, "") for c in columns])
    for row in cells:
        table.add_row(*(Text("-" if v is None else str(v)) for v in row.values()))
    print_result(table, json_mode=False)


def _run(
    ctx: typer.Context,
    json_output: bool,
    read: Callable[[Conn, Settings], Coroutine[Any, Any, list[DebugRow]]],
) -> list[DebugRow]:
    """Connect (migrations checked), read in one READ ONLY transaction."""
    from manta_trading.data.tick.active_contract import roll_conninfo
    from manta_trading.data.tick.store_context import connect_migrated, database_url

    settings: Settings = ctx.obj["settings"]

    async def main() -> list[DebugRow]:
        conn = await connect_migrated(roll_conninfo(database_url(settings)))
        try:
            await conn.set_read_only(True)
            async with conn.transaction():
                return await read(conn, settings)
        finally:
            await conn.close()

    return run_mapped(main, json_output, read_storage())


def _symbols(product: str | None) -> tuple[str, ...] | None:
    return None if product is None else universe_entries(product)[0].symbols


@debug_app.command("requests", epilog=DEBUG_EPILOG)
def debug_requests(
    ctx: typer.Context,
    product: str | None = PRODUCT_OPTION,
    start: datetime | None = _START_OPTION,
    end: datetime | None = _END_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """tick_request rows: shape, range, adoption, job, costs, times."""
    from manta_trading.data.tick.debug_reads import requests

    async def read(conn: Conn, _: Settings) -> list[DebugRow]:
        return await requests(conn, _symbols(product), _day(start), _day(end))

    _print("requests", _run(ctx, json_output, read), json_output)


@debug_app.command("units", epilog=DEBUG_EPILOG)
def debug_units(
    ctx: typer.Context,
    product: str | None = PRODUCT_OPTION,
    start: datetime | None = _START_OPTION,
    end: datetime | None = _END_OPTION,
    state: str | None = _STATE_OPTION,
    fetch_status: str | None = _FETCH_STATUS_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """tick_archive_unit rows with their request's schema: state, failure,
    attempts, file, record counts, lineage."""
    from manta_trading.data.tick.debug_reads import units

    chosen = _member(UnitState, state, "--state", json_output)
    status = _member(FetchStatus, fetch_status, "--fetch-status", json_output)

    async def read(conn: Conn, _: Settings) -> list[DebugRow]:
        span = (_day(start), _day(end))
        return await units(conn, _symbols(product), span, chosen, status)

    _print("units", _run(ctx, json_output, read), json_output)


async def _session_open(settings: Settings, product: str, day: date) -> int:
    from manta_trading.data.tick.get_plan import sessions_dated
    from manta_trading.data.tick.ingest_records import utc_ns
    from manta_trading.data.tick.store_context import TickPreflightError
    from manta_trading.data.tick.tick_calendar import calendar_url

    found = await sessions_dated(
        calendar_url(settings), product, day, day + timedelta(days=1)
    )
    if not found:
        raise TickPreflightError(f"no {product} session is dated {day}")
    return utc_ns(found[0].open_utc)


@debug_app.command("definitions", epilog=DEBUG_EPILOG)
def debug_definitions(
    ctx: typer.Context,
    product: str | None = PRODUCT_OPTION,
    symbol: str | None = _SYMBOL_OPTION,
    as_of: datetime | None = _AS_OF_OPTION,
    spreads: bool = _SPREADS_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """tick_definition rows: instrument, symbol, class, validity, tick size,
    multiplier, currency, unit."""
    from manta_trading.data.tick.debug_reads import definitions

    if as_of is not None and product is None:
        refuse("--as-of needs --product (its calendar dates the session)", json_output)

    async def read(conn: Conn, settings: Settings) -> list[DebugRow]:
        asset = None if product is None else universe_entries(product)[0].product
        at_ns = None
        if as_of is not None and asset is not None:
            at_ns = await _session_open(settings, asset, as_of.date())
        return await definitions(conn, asset, symbol, at_ns, spreads)

    _print("definitions", _run(ctx, json_output, read), json_output)
