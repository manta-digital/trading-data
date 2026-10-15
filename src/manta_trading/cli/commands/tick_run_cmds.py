"""``mt data tick run``: the production tick pass (slice 234 TD2).

The verb the ``mt-tick-pass.service`` unit runs. It takes no options: it always
covers the full universe (``pass --start/--end`` stay the by-hand narrowing).
The sequencing and recording live in ``cli/tick_run.py``; this module binds the
two real passes and prints their existing renderings.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import typer

from manta_trading.cli.commands.tick_exit import (
    EXIT_BY_OUTCOME,
    EXIT_IN_FLIGHT,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_PREFLIGHT,
    EXIT_PROVIDER,
    EXIT_REFUSED,
    EXIT_STORAGE,
)
from manta_trading.cli.output import print_error

if TYPE_CHECKING:
    from manta_trading.cli.commands.tick_heartbeat import IngestHeartbeat
    from manta_trading.cli.tick_run import PassEnd, PhaseHook
    from manta_trading.config import Settings
    from manta_trading.data.tick.pass_contract import (
        PassPhase,
        PassResult,
        TickPassPhaseName,
    )
    from manta_trading.data.tick.run_context import TickRun

RUN_EPILOG = (
    "Runs the acquisition pass over the full universe, then the ingest pass "
    "whatever acquisition did, as one pass_runs row (kind tick). The exit "
    f"code is the worse of the two outcomes: {EXIT_OK} ok; {EXIT_PREFLIGHT} "
    "either pass refused at preflight (a setting, a pending migration, a held "
    f"lock, the tick database unreachable); {EXIT_PROVIDER} provider abort; "
    f"{EXIT_PARTIAL} some units failed; {EXIT_STORAGE} storage abort; "
    f"{EXIT_REFUSED} a guard refused the purchase; {EXIT_IN_FLIGHT} jobs "
    "still processing at the wait budget."
)


class _Reported:
    """A pass phase that writes its name to the row before it runs."""

    def __init__(self, inner: Any, hook: PhaseHook) -> None:
        self._inner = inner
        self._hook = hook
        self.name: TickPassPhaseName = inner.name

    async def run(self, run: TickRun) -> Any:
        await self._hook(self.name)
        return await self._inner.run(run)


async def run_acquisition(
    run: TickRun, phases: Sequence[PassPhase[TickRun]], hook: PhaseHook
) -> PassResult:
    """Run the acquisition ``phases`` over ``run``, each reported as it starts."""
    from manta_trading.data.tick.pass_contract import TickPass
    from manta_trading.data.tick.run_context import TickRun

    reported = [_Reported(phase, hook) for phase in phases]
    return await TickPass[TickRun](run, reported).run()


async def acquire_full_universe(settings: Settings, hook: PhaseHook) -> PassResult:
    """``mt data tick pass`` with no window and buying allowed."""
    from manta_trading.data.tick import acquisition_pass, run_context
    from manta_trading.data.tick.databento.dbn_file import DbnFileReader

    phases = acquisition_pass.pass_phases((None, None), False, DbnFileReader())
    async with run_context.open_tick_run(settings) as run:
        return await run_acquisition(run, phases, hook)


def _print_end(end: PassEnd, print_result: Any) -> None:
    if end.result is None:
        print_error(f"{end.label} refused: {end.refusal}", json_mode=False)
        return
    print_result(end.result, EXIT_BY_OUTCOME[end.result.outcome])


def tick_run(ctx: typer.Context) -> None:
    """Acquire then ingest the full universe, recorded as one tick pass run."""
    from manta_trading.cli.commands._pass_run import pass_run_recorder
    from manta_trading.cli.commands.tick_ingest_render import print_ingest
    from manta_trading.cli.commands.tick_pass_render import print_pass
    from manta_trading.cli.commands.tick_store_cmds import ingest_units, run_mapped
    from manta_trading.cli.tick_run import run_tick
    from manta_trading.data.tick.constants import TICK_INGEST_WORKERS

    settings: Settings = ctx.obj["settings"]

    async def ingest(heartbeat: IngestHeartbeat | None) -> PassResult:
        return await ingest_units(settings, (), heartbeat)

    with pass_run_recorder(settings) as recorder:
        result = run_mapped(
            lambda: run_tick(
                recorder,
                lambda hook: acquire_full_universe(settings, hook),
                ingest,
            ),
            False,
        )
    _print_end(result.acquire, lambda r, code: print_pass(r, code, json_mode=False))
    _print_end(
        result.ingest,
        lambda r, code: print_ingest(r, code, TICK_INGEST_WORKERS, json_mode=False),
    )
    typer.echo(f"tick run: {result.detail} (exit {result.exit_code})")
    if result.exit_code != EXIT_OK:
        raise typer.Exit(result.exit_code)
