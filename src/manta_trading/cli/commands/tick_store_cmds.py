"""``mt data tick`` verbs that need no provider: ``ingest``, ``status``, ``coverage``.

Slice 225. No Databento key. ``ingest`` runs on ``open_tick_store`` (lock,
archive); ``status`` and ``coverage`` only read, through ``connect_migrated``
(no lock, no archive directory). All are registered on ``tick_app`` in
``tick.py``. :func:`run_mapped` is the one place a tick verb's
run-level failures become exit codes (``tick_exit.exit_code_for``); ``tick.py``'s
writers use it too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import TYPE_CHECKING, Any

import typer

from manta_trading.cli.commands.tick_exit import (
    EXIT_BY_OUTCOME,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_PREFLIGHT,
    EXIT_STORAGE,
    exit_code_for,
)
from manta_trading.cli.output import print_error

if TYPE_CHECKING:
    from manta_trading.config import Settings
    from manta_trading.data.tick.pass_contract import PassResult
    from manta_trading.data.tick.tick_coverage import TickCoverage
    from manta_trading.data.tick.tick_status_build import TickStatus
    from manta_trading.data.tick.universe import TickUniverseEntry

READ_EPILOG = (
    f"Exit codes: {EXIT_OK} ok; {EXIT_PREFLIGHT} preflight (a setting, a pending "
    "tick migration, an unknown --product, --end not after --start, or dates "
    "past the calendar's populated sessions: run mt data extend); "
    f"{EXIT_PARTIAL} coverage found a raw-count mismatch; {EXIT_STORAGE} the tick "
    "or production (calendar) database is unreachable."
)
INGEST_EPILOG = (
    f"Exit codes: {EXIT_OK} ok (skips included); {EXIT_PREFLIGHT} preflight (a "
    "setting, a pending tick migration, the ingest lock, or the tick database "
    f"unreachable at start); {EXIT_PARTIAL} some units failed a check (see each "
    f"reason; fix, then mt data tick reset); {EXIT_STORAGE} storage (the tick "
    "database lost or hung during the run, or the calendar unreachable)."
)
_UNIT_ID_OPTION = typer.Option(
    [], "--unit-id", help="Ingest only this unit; repeatable. Never overrides a rule."
)
_JSON_OPTION = typer.Option(False, "--json", help="Output as JSON.")
_PRODUCT_OPTION = typer.Option(None, "--product", help="One universe product.")
_ALL_INSTRUMENTS_OPTION = typer.Option(
    False, "--all-instruments", help="List spreads beside the outrights."
)
_DATE_FORMAT = "%Y-%m-%d"
_START_OPTION = typer.Option(
    ..., "--start", formats=[_DATE_FORMAT], help="First session date."
)
_END_OPTION = typer.Option(
    ..., "--end", formats=[_DATE_FORMAT], help="End session date, EXCLUSIVE."
)


def run_mapped[T](
    main: Callable[[], Coroutine[Any, Any, T]],
    json_output: bool,
    storage: tuple[type[Exception], ...] = (),
) -> T:
    """Run ``main`` and map run-level failures to their exit codes.

    ``storage`` names extra failures that exit as storage for this verb.
    """
    try:
        return asyncio.run(main())
    except Exception as exc:  # process boundary: every mapped class exits
        code = EXIT_STORAGE if isinstance(exc, storage) else exit_code_for(exc)
        if code is None:
            raise
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(code) from exc


async def _ingest(settings: Settings, unit_ids: tuple[int, ...]) -> PassResult:
    from manta_trading.data.tick import constants
    from manta_trading.data.tick.databento.dbn_file import DbnFileReader
    from manta_trading.data.tick.ingest_pass import IngestInputs, IngestPhase
    from manta_trading.data.tick.ingest_worker import WorkerConnectionSettings
    from manta_trading.data.tick.pass_contract import TickPass
    from manta_trading.data.tick.store_context import (
        TickStore,
        database_url,
        open_tick_store,
    )
    from manta_trading.data.tick.tick_calendar import calendar_url

    async with open_tick_store(
        settings, lock_key=constants.TICK_INGEST_LOCK_KEY
    ) as store:
        inputs = IngestInputs(
            reader=DbnFileReader(),
            worker_settings=WorkerConnectionSettings(
                url=database_url(settings),
                connect_timeout_seconds=constants.TICK_DB_CONNECT_TIMEOUT_SECONDS,
                keepalives_idle_seconds=constants.TICK_DB_KEEPALIVES_IDLE_SECONDS,
                keepalives_interval_seconds=(
                    constants.TICK_DB_KEEPALIVES_INTERVAL_SECONDS
                ),
                keepalives_count=constants.TICK_DB_KEEPALIVES_COUNT,
                lock_timeout_seconds=constants.TICK_INGEST_LOCK_TIMEOUT_SECONDS,
            ),
            calendar_url=calendar_url(settings),
            workers=constants.TICK_INGEST_WORKERS,
            unit_ids=unit_ids,
        )
        return await TickPass[TickStore](store, [IngestPhase(inputs)]).run()


def tick_ingest(
    ctx: typer.Context,
    unit_ids: list[int] = _UNIT_ID_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Load verified trades/tbbo units into tick_trade, checked, one transaction each.

    No provider call and no Databento key: files come from the archive.
    """
    from manta_trading.cli.commands.tick_ingest_render import print_ingest
    from manta_trading.data.tick.constants import TICK_INGEST_WORKERS

    settings: Settings = ctx.obj["settings"]
    result = run_mapped(lambda: _ingest(settings, tuple(unit_ids)), json_output)
    exit_code = EXIT_BY_OUTCOME[result.outcome]
    print_ingest(result, exit_code, TICK_INGEST_WORKERS, json_mode=json_output)
    if exit_code != EXIT_OK:
        raise typer.Exit(exit_code)


# -- read-only verbs: status and coverage ------------------------------------------


def _entries(product: str | None) -> list[TickUniverseEntry]:
    from manta_trading.data.tick.store_context import TickPreflightError
    from manta_trading.data.tick.universe import TICK_UNIVERSE

    entries = [e for e in TICK_UNIVERSE if product in (None, e.product)]
    if not entries:
        known = ", ".join(e.product for e in TICK_UNIVERSE)
        raise TickPreflightError(
            f"--product {product} is not in the universe ({known})"
        )
    return entries


async def _read[T](
    settings: Settings, read: Callable[[Any, str], Coroutine[Any, Any, T]]
) -> T:
    """Connect (migrations checked; no lock, no archive) and run ``read``."""
    from manta_trading.data.tick.store_context import connect_migrated, database_url
    from manta_trading.data.tick.tick_calendar import calendar_url

    conn = await connect_migrated(database_url(settings))
    try:
        return await read(conn, calendar_url(settings))
    finally:
        await conn.close()


def _read_storage() -> tuple[type[Exception], ...]:
    from manta_trading.data.tick.store_context import TickDatabaseUnreachable

    return (TickDatabaseUnreachable,)


def tick_status(
    ctx: typer.Context,
    product: str | None = _PRODUCT_OPTION,
    all_instruments: bool = _ALL_INSTRUMENTS_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Sessions held per product: complete, awaiting ingest, in flight, holes.

    Reads the manifest, ledger, conditions and edge; never scans tick_trade.
    """
    from manta_trading.cli.commands.tick_status_render import print_status
    from manta_trading.data.tick.store_context import utc_now
    from manta_trading.data.tick.tick_status_build import build_status

    settings: Settings = ctx.obj["settings"]

    async def main() -> TickStatus:
        entries = _entries(product)
        return await _read(
            settings,
            lambda conn, url: build_status(
                conn, url, entries, utc_now(), all_instruments=all_instruments
            ),
        )

    status = run_mapped(main, json_output, _read_storage())
    print_status(status, EXIT_OK, json_mode=json_output)


def tick_coverage(
    ctx: typer.Context,
    start: datetime = _START_OPTION,
    end: datetime = _END_OPTION,
    product: str | None = _PRODUCT_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """The raw-count proof: tick_trade rows per session against the ledger."""
    from manta_trading.cli.commands.tick_status_render import print_coverage
    from manta_trading.data.tick.tick_coverage import build_coverage

    if end <= start:
        print_error(
            f"--end {end:%Y-%m-%d} must be after --start {start:%Y-%m-%d}"
            " (end is exclusive)",
            json_mode=json_output,
        )
        raise typer.Exit(EXIT_PREFLIGHT)
    settings: Settings = ctx.obj["settings"]

    async def main() -> list[TickCoverage]:
        entries = _entries(product)

        async def read(conn: Any, url: str) -> list[TickCoverage]:
            return [
                await build_coverage(conn, url, e, start.date(), end.date())
                for e in entries
            ]

        return await _read(settings, read)

    results = run_mapped(main, json_output, _read_storage())
    exit_code = EXIT_PARTIAL if any(r.mismatched for r in results) else EXIT_OK
    print_coverage(results, exit_code, json_mode=json_output)
    if exit_code != EXIT_OK:
        raise typer.Exit(exit_code)
