"""``mt data tick`` verbs that need no provider: ``ingest`` (slice 225).

They run on ``open_tick_store`` — no Databento key — and are registered on
``tick_app`` in ``tick.py``. :func:`run_mapped` is the one place a tick verb's
run-level failures become exit codes (``tick_exit.exit_code_for``); ``tick.py``'s
writers use it too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
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

INGEST_EPILOG = (
    f"Exit codes: {EXIT_OK} ok (skips included); {EXIT_PREFLIGHT} preflight (a "
    "setting, a pending tick migration, the ingest lock); "
    f"{EXIT_PARTIAL} some units failed a check (see each reason; fix, then "
    f"mt data tick reset); {EXIT_STORAGE} storage (tick database or calendar)."
)
_UNIT_ID_OPTION = typer.Option(
    [], "--unit-id", help="Ingest only this unit; repeatable. Never overrides a rule."
)
_JSON_OPTION = typer.Option(False, "--json", help="Output as JSON.")


def run_mapped[T](main: Callable[[], Coroutine[Any, Any, T]], json_output: bool) -> T:
    """Run ``main`` and map run-level failures to their exit codes."""
    try:
        return asyncio.run(main())
    except Exception as exc:  # process boundary: every mapped class exits
        code = exit_code_for(exc)
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
