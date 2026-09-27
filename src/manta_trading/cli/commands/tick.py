"""``mt data tick`` — futures tick data (initiative 220).

One verb so far, ``estimate``: a cost-and-size preflight over Databento's free
metadata endpoints. It buys nothing. Exit codes are defined here and nowhere
else (slice 220 design, *CLI verb*); Rich rendering lives in
``tick_render.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import typer

from manta_trading.cli.commands.tick_render import print_estimate
from manta_trading.cli.output import print_error
from manta_trading.data.tick.constants import CME_DATASET, ESTIMATE_SCHEMAS, SType
from manta_trading.data.tick.estimate import EstimateRefusedError, build_estimate
from manta_trading.data.tick.provider import TickRequest
from manta_trading.providers.errors import ProviderAuthError, ProviderError

if TYPE_CHECKING:
    from manta_trading.config import Settings

# Exit codes. Integers live here only.
EXIT_OK = 0
EXIT_PREFLIGHT = 1
EXIT_PROVIDER = 2

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
    help="Futures tick data (Databento): cost preflight.",
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
