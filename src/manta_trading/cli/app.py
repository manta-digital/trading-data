"""Typer CLI application definition for Manta Trading."""

from __future__ import annotations

import importlib.metadata

import typer

from manta_trading.cli.commands.config import config_app
from manta_trading.cli.commands.data import data_app
from manta_trading.cli.commands.provider import provider_app
from manta_trading.cli.commands.serve import serve
from manta_trading.cli.commands.status import status_app
from manta_trading.cli.commands.update import update
from manta_trading.config import Settings
from manta_trading.constants import DISTRIBUTION_NAME
from manta_trading.logging import get_logger, setup_logging

logger = get_logger(__name__)

app = typer.Typer(
    name="mt",
    help="Manta Trading CLI",
    no_args_is_help=True,
)

app.add_typer(status_app, name="status")
app.add_typer(config_app, name="config")
app.add_typer(data_app, name="data")
app.add_typer(provider_app, name="provider")
app.command(name="serve")(serve)
app.command(name="update")(update)


# -- Version callback ---------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        try:
            version = importlib.metadata.version(DISTRIBUTION_NAME)
        except importlib.metadata.PackageNotFoundError:
            logger.warning(
                "No installed distribution metadata found for %r; "
                "reporting version as 'dev'.",
                DISTRIBUTION_NAME,
            )
            version = "dev"
        typer.echo(f"mt version {version}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Manta Trading CLI."""
    ctx.ensure_object(dict)
    settings = Settings()
    setup_logging(settings)
    ctx.obj["settings"] = settings
