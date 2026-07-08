"""config subcommand — manage persistent configuration."""

from __future__ import annotations

import typer
from rich import print as rprint

from manta_trading.cli.output import make_table, print_error, print_result
from manta_trading.config.keys import CONFIG_KEYS
from manta_trading.config.manager import (
    get_config,
    project_config_path,
    set_config,
    user_config_path,
)

config_app = typer.Typer(
    name="config",
    help="Manage configuration",
    no_args_is_help=True,
)


@config_app.command("list")
def config_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    cwd: str = typer.Option(".", "--cwd", help="Working directory"),
) -> None:
    """Show all config keys with current values and sources."""
    rows = []
    for key_name in sorted(CONFIG_KEYS):
        value, source = get_config(key_name, cwd=cwd)
        rows.append(
            {
                "key": key_name,
                "value": str(value) if value is not None else None,
                "source": source,
                "description": CONFIG_KEYS[key_name].description,
            }
        )

    if json_output:
        print_result(rows, json_mode=True)
        return

    table = make_table(
        "Configuration",
        [("Key", "cyan"), ("Value", ""), ("Source", "dim"), ("Description", "dim")],
    )
    for row in rows:
        table.add_row(
            row["key"],
            row["value"] if row["value"] is not None else "(not set)",
            row["source"],
            row["description"],
        )
    rprint(table)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(help="Config key to read"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    cwd: str = typer.Option(".", "--cwd", help="Working directory"),
) -> None:
    """Show the resolved value of a config key."""
    try:
        value, source = get_config(key, cwd=cwd)
    except KeyError as exc:
        if json_output:
            print_error(str(exc), json_mode=True)
        else:
            rprint(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if json_output:
        print_result(
            {
                "key": key,
                "value": str(value) if value is not None else None,
                "source": source,
            },
            json_mode=True,
        )
        return

    display_val = str(value) if value is not None else "(not set)"
    rprint(f"{key} = {display_val}  (source: {source})")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(help="Config key to set"),
    value: str = typer.Argument(help="Value to set"),
    project: bool = typer.Option(
        False, "--project", help="Write to project-level config"
    ),
    cwd: str = typer.Option(".", "--cwd", help="Working directory"),
) -> None:
    """Set a config value."""
    try:
        set_config(key, value, project=project, cwd=cwd)
    except (KeyError, ValueError) as exc:
        rprint(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    source = "project" if project else "user"
    rprint(f"Set {key} = {value} ({source} config)")


@config_app.command("path")
def config_path(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    cwd: str = typer.Option(".", "--cwd", help="Working directory"),
) -> None:
    """Show config file locations and existence status."""
    user_path = user_config_path()
    proj_path = project_config_path(cwd)

    user_exists = user_path.is_file()
    proj_exists = proj_path.is_file()

    if json_output:
        print_result(
            {
                "user": {"path": str(user_path), "exists": user_exists},
                "project": {"path": str(proj_path), "exists": proj_exists},
            },
            json_mode=True,
        )
        return

    user_status = "[green]exists[/green]" if user_exists else "[dim]not found[/dim]"
    proj_status = "[green]exists[/green]" if proj_exists else "[dim]not found[/dim]"

    rprint(f"  User:    {user_path}  {user_status}")
    rprint(f"  Project: {proj_path}  {proj_status}")
