"""``mt data tick`` — futures tick data (initiative 220).

``estimate`` (220): a cost-and-size preflight over Databento's free metadata
endpoints. ``adopt`` and ``reset`` (223): manifest writers that run inside
``open_tick_run`` (preflight and advisory lock). ``pass`` (224): the acquisition
pass — the only verb that can buy, and only within both spend ceilings.
``ingest``, ``status`` and ``coverage`` (225): provider-free, in
``tick_store_cmds.py``.
Exit codes are defined once, in ``tick_exit.py`` (slice 220 design, *CLI verb*);
Rich rendering lives in ``tick_render.py`` and ``tick_pass_render.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

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
from manta_trading.cli.commands.tick_render import print_estimate
from manta_trading.cli.commands.tick_store_cmds import (
    INGEST_EPILOG,
    READ_EPILOG,
    run_mapped,
    tick_coverage,
    tick_ingest,
    tick_status,
)
from manta_trading.cli.output import print_error
from manta_trading.data.tick.constants import CME_DATASET, ESTIMATE_SCHEMAS, SType
from manta_trading.data.tick.estimate import EstimateRefusedError, build_estimate
from manta_trading.data.tick.provider import TickRequest
from manta_trading.providers.errors import ProviderAuthError, ProviderError

if TYPE_CHECKING:
    from manta_trading.config import Settings
    from manta_trading.data.tick.run_context import TickRun

_DATE_FORMAT = "%Y-%m-%d"

_EXIT_EPILOG = (
    f"Exit codes: {EXIT_OK} ok; {EXIT_PREFLIGHT} preflight (missing key, end not "
    f"after start, end past the dataset's available end); {EXIT_PROVIDER} "
    "provider error. Click's own usage errors (a missing required option, an "
    f"--stype outside the choices) also exit {EXIT_PROVIDER}, before the "
    "command runs."
)

tick_app = typer.Typer(
    name="tick",
    help="Futures tick data (Databento): cost preflight, adoption, reset, pass, "
    "ingest, status, coverage.",
    no_args_is_help=True,
)

_SYMBOLS_OPTION = typer.Option(
    ...,
    "--symbols",
    help="Comma-separated symbols in the --stype symbology, e.g. ES.c.0.",
)
_STYPE_OPTION = typer.Option(
    ..., "--stype", help="Input symbology of --symbols.", case_sensitive=False
)
_START_OPTION = typer.Option(
    ..., "--start", formats=[_DATE_FORMAT], help="First UTC day (YYYY-MM-DD)."
)
_END_OPTION = typer.Option(
    ...,
    "--end",
    formats=[_DATE_FORMAT],
    help="End UTC day (YYYY-MM-DD), EXCLUSIVE: --end 2025-01-11 stops after "
    "2025-01-10.",
)
_JSON_OPTION = typer.Option(False, "--json", help="Output as JSON.")


def _request(symbols: str, stype: SType, start: date, end: date) -> TickRequest:
    """Build the request; ``ValueError`` on an empty symbol list or end ≤ start."""
    return TickRequest(
        dataset=CME_DATASET,
        symbols=tuple(s.strip() for s in symbols.split(",") if s.strip()),
        stype_in=stype,
        schema=ESTIMATE_SCHEMAS[0],
        start=start,
        end=end,
    )


@tick_app.command("estimate", epilog=_EXIT_EPILOG)
def tick_estimate(
    ctx: typer.Context,
    symbols: str = _SYMBOLS_OPTION,
    stype: SType = _STYPE_OPTION,
    start: datetime = _START_OPTION,
    end: datetime = _END_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Records, billable size, and cost per schema tier — free calls only."""
    # Imported here, not at module level: ``databento`` (with pandas and
    # pyarrow) adds ~0.15 s to every ``mt`` command's startup otherwise.
    from manta_trading.data.tick.databento.adapter import DatabentoTickProvider

    settings: Settings = ctx.obj["settings"]
    try:
        request = _request(symbols, stype, start.date(), end.date())
        provider = DatabentoTickProvider.from_settings(settings)
    except (ValueError, ProviderAuthError) as exc:
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(EXIT_PREFLIGHT) from exc
    with provider:
        try:
            report = build_estimate(provider, request, settings.tick_spend_ceiling_usd)
        except EstimateRefusedError as exc:
            print_error(str(exc), json_mode=json_output)
            raise typer.Exit(EXIT_PREFLIGHT) from exc
        except ProviderError as exc:
            print_error(f"provider error: {exc}", json_mode=json_output)
            raise typer.Exit(EXIT_PROVIDER) from exc
    print_estimate(report, json_mode=json_output)


# -- manifest writers (slice 223) ------------------------------------------------

_WRITER_EPILOG = (
    f"Exit codes: {EXIT_OK} ok; {EXIT_PREFLIGHT} preflight or refusal (a setting, "
    "a pending tick migration, the run lock, or an adoption refused before any "
    f"row was written); {EXIT_PROVIDER} provider error (an adopt interrupted "
    f"while verifying resumes when run again); {EXIT_PARTIAL} adopt "
    f"finished but some units failed verification; {EXIT_STORAGE} storage "
    "(archive write, calendar, or tick database)."
)
_CONFIRM_WORD = "reset"

_JOB_ID_OPTION = typer.Option(..., "--job-id", help="Databento batch job id.")
_SOURCE_OPTION = typer.Option(
    ..., "--source", help="The job's directory or the provider's zip."
)
_UNIT_ID_OPTION = typer.Option(
    [], "--unit-id", help="A unit to reset or reopen; repeatable."
)
_ALL_OPTION = typer.Option(False, "--all", help="Every eligible unit.")
_YES_OPTION = typer.Option(False, "--yes", "-y", help="Skip the confirmation.")


def _run_writer[T](
    settings: Settings, core: Callable[[TickRun], Awaitable[T]], json_output: bool
) -> T:
    """Open the run context, run ``core`` in it, and map failures to exit codes."""
    from manta_trading.data.tick import run_context

    async def run() -> T:
        async with run_context.open_tick_run(settings) as tick_run:
            return await core(tick_run)

    return run_mapped(run, json_output)


@tick_app.command("adopt", epilog=_WRITER_EPILOG)
def tick_adopt(
    ctx: typer.Context,
    job_id: str = _JOB_ID_OPTION,
    source: Path = _SOURCE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Archive and record a batch job already on disk — free calls only."""
    from manta_trading.cli.commands.tick_pass_render import print_adopt
    from manta_trading.data.tick import adopt
    from manta_trading.data.tick.databento.dbn_file import DbnFileReader

    settings: Settings = ctx.obj["settings"]
    result = _run_writer(
        settings,
        lambda run: adopt.adopt_job(run, job_id, source, DbnFileReader()),
        json_output,
    )
    print_adopt(result, json_mode=json_output)
    if result.verify_failures:
        raise typer.Exit(EXIT_PARTIAL)


@tick_app.command("reset", epilog=_WRITER_EPILOG)
def tick_reset(
    ctx: typer.Context,
    unit_ids: list[int] = _UNIT_ID_OPTION,
    every: bool = _ALL_OPTION,
    yes: bool = _YES_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Reset exhausted units to UNKNOWN and reopen holed ones."""
    from manta_trading.cli.commands.tick_pass_render import print_reset
    from manta_trading.data.tick import reset

    if (every and unit_ids) or (not every and not unit_ids):
        print_error(
            "give either --unit-id (repeatable) or --all", json_mode=json_output
        )
        raise typer.Exit(EXIT_PREFLIGHT)
    if not yes and not json_output:  # --json skips the prompt, as minute reset
        target = "every eligible unit" if every else f"unit(s) {unit_ids}"
        typed = typer.prompt(f"Reset {target}? Type '{_CONFIRM_WORD}'", default="")
        if typed.strip().lower() != _CONFIRM_WORD:
            print_error("Operator declined; nothing changed.", json_mode=False)
            raise typer.Exit(EXIT_PREFLIGHT)
    settings: Settings = ctx.obj["settings"]
    targets = reset.ALL if every else unit_ids
    changes = _run_writer(
        settings, lambda run: reset.reset_units(run, targets), json_output
    )
    print_reset(changes, json_mode=json_output)


# -- the acquisition pass (slice 224) ----------------------------------------------

_PASS_EPILOG = (
    f"Exit codes: {EXIT_OK} ok; {EXIT_PREFLIGHT} preflight or bad window; "
    f"{EXIT_PROVIDER} provider abort; {EXIT_PARTIAL} some units failed; "
    f"{EXIT_STORAGE} storage abort; {EXIT_REFUSED} a guard refused the purchase "
    f"(nothing bought); {EXIT_IN_FLIGHT} jobs still processing at the wait "
    "budget (run again to continue)."
)
_WINDOW_START_OPTION = typer.Option(
    None, "--start", formats=[_DATE_FORMAT], help="Narrow to days from here."
)
_WINDOW_END_OPTION = typer.Option(
    None,
    "--end",
    formats=[_DATE_FORMAT],
    help="Narrow to days before here (EXCLUSIVE).",
)
_ESTIMATE_ONLY_OPTION = typer.Option(
    False, "--estimate-only", help="Plan and cost the run; buy nothing."
)


@tick_app.command("pass", epilog=_PASS_EPILOG)
def tick_pass(
    ctx: typer.Context,
    start: datetime | None = _WINDOW_START_OPTION,
    end: datetime | None = _WINDOW_END_OPTION,
    estimate_only: bool = _ESTIMATE_ONLY_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Reconcile, plan, buy within both spend ceilings, await, project definitions.

    --start/--end only narrow the configured universe; they never widen it.
    """
    from manta_trading.cli.commands.tick_pass_render import print_pass
    from manta_trading.data.tick import acquisition_pass
    from manta_trading.data.tick.databento.dbn_file import DbnFileReader

    window = (start.date() if start else None, end.date() if end else None)
    if window[0] and window[1] and window[1] <= window[0]:
        print_error(
            f"--end {window[1]} must be after --start {window[0]} (end is exclusive)",
            json_mode=json_output,
        )
        raise typer.Exit(EXIT_PREFLIGHT)
    settings: Settings = ctx.obj["settings"]
    result = _run_writer(
        settings,
        lambda run: acquisition_pass.run_pass(
            run, window, estimate_only, DbnFileReader()
        ),
        json_output,
    )
    exit_code = EXIT_BY_OUTCOME[result.outcome]
    print_pass(result, exit_code, json_mode=json_output)
    if exit_code != EXIT_OK:
        raise typer.Exit(exit_code)


# -- provider-free verbs (slice 225) -----------------------------------------------

tick_app.command("ingest", epilog=INGEST_EPILOG)(tick_ingest)
tick_app.command("status", epilog=READ_EPILOG)(tick_status)
tick_app.command("coverage", epilog=READ_EPILOG)(tick_coverage)
