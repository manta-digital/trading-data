"""provider subcommand — data provider management and introspection."""

from __future__ import annotations

import typer

from manta_trading.cli.output import make_table, print_error, print_result
from manta_trading.logging import get_logger
from manta_trading.providers.auth import resolve_auth
from manta_trading.providers.profiles import (
    get_all_profiles,
    get_profile,
    resolve_alias,
)

logger = get_logger(__name__)

provider_app = typer.Typer(
    name="provider",
    help="Data provider management",
    no_args_is_help=True,
)


@provider_app.command("list")
def provider_list(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show all registered providers with auth status."""
    settings = ctx.obj["settings"]
    profiles = get_all_profiles()
    rows = []

    for name in sorted(profiles):
        profile = profiles[name]
        auth = resolve_auth(profile, settings)
        rows.append(
            {
                "name": profile.name,
                "provider_type": str(profile.provider_type),
                "description": profile.description,
                "aliases": ", ".join(profile.aliases) if profile.aliases else "",
                "auth_valid": auth.is_valid(),
                "base_url": profile.base_url or "",
            }
        )

    if json_output:
        print_result(rows, json_mode=True)
        return

    table = make_table(
        "Providers",
        [
            ("Name", "cyan"),
            ("Type", ""),
            ("Description", ""),
            ("Aliases", "dim"),
            ("Auth", ""),
        ],
    )
    for row in rows:
        auth_icon = "[green]✓[/green]" if row["auth_valid"] else "[red]✗[/red]"
        table.add_row(
            row["name"],
            row["provider_type"],
            row["description"],
            row["aliases"],
            auth_icon,
        )
    print_result(table, json_mode=False)


@provider_app.command("status")
def provider_status(
    ctx: typer.Context,
    name: str = typer.Argument(None, help="Provider name or alias"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show detailed status for one or all providers."""
    settings = ctx.obj["settings"]

    if name is not None:
        try:
            canonical = resolve_alias(name)
        except KeyError as exc:
            print_error(str(exc), json_mode=json_output)
            raise typer.Exit(code=1) from None
        profiles = {canonical: get_profile(canonical)}
    else:
        profiles = get_all_profiles()

    results = []
    for canonical_name in sorted(profiles):
        profile = profiles[canonical_name]
        auth = resolve_auth(profile, settings)
        results.append(
            {
                "name": profile.name,
                "provider_type": str(profile.provider_type),
                "base_url": profile.base_url,
                "api_key_env": profile.api_key_env,
                "rate_limit": (
                    {
                        "requests_per_minute": profile.rate_limit.requests_per_minute,
                        "daily_limit": profile.rate_limit.daily_limit,
                    }
                    if profile.rate_limit
                    else None
                ),
                "aliases": list(profile.aliases),
                "auth_type": str(profile.auth_type),
                "auth_valid": auth.is_valid(),
                "active_source": auth.active_source,
                "setup_hint": auth.setup_hint,
            }
        )

    if json_output:
        data = results[0] if name is not None else results
        print_result(data, json_mode=True)
        return

    for info in results:
        auth_icon = "[green]✓[/green]" if info["auth_valid"] else "[red]✗[/red]"
        lines = [
            f"[cyan bold]{info['name']}[/cyan bold]",
            f"  Type:       {info['provider_type']}",
            f"  Base URL:   {info['base_url'] or 'n/a'}",
            f"  Auth:       {auth_icon} {info['active_source'] or info['setup_hint']}",
        ]
        if info["rate_limit"]:
            rl = info["rate_limit"]
            limit_str = f"{rl['requests_per_minute']} req/min"
            if rl["daily_limit"]:
                limit_str += f", {rl['daily_limit']}/day"
            lines.append(f"  Rate Limit: {limit_str}")
        if info["aliases"]:
            lines.append(f"  Aliases:    {', '.join(info['aliases'])}")
        print_result("\n".join(lines), json_mode=False)


@provider_app.command("test")
def provider_test(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Provider name or alias"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Validate credentials for a specific provider."""
    settings = ctx.obj["settings"]

    try:
        canonical = resolve_alias(name)
    except KeyError as exc:
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(code=1) from None

    profile = get_profile(canonical)
    auth = resolve_auth(profile, settings)

    result = {
        "provider": profile.name,
        "auth_valid": auth.is_valid(),
        "active_source": auth.active_source,
        "setup_hint": auth.setup_hint,
    }

    if json_output:
        print_result(result, json_mode=True)
        return

    if auth.is_valid():
        print_result(
            f"[green]✓[/green] {profile.name}: authenticated via {auth.active_source}",
            json_mode=False,
        )
    else:
        print_result(
            f"[red]✗[/red] {profile.name}: not authenticated — {auth.setup_hint}",
            json_mode=False,
        )
