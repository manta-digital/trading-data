"""status subcommand — system health overview."""

from __future__ import annotations

from urllib.parse import urlparse

import typer

from manta_trading.cli.output import make_table, print_result
from manta_trading.logging import get_logger
from manta_trading.providers.auth import resolve_auth
from manta_trading.providers.profiles import get_all_profiles

logger = get_logger(__name__)

status_app = typer.Typer(
    name="status",
    help="System status and health",
    no_args_is_help=False,
)


def _redact_url(url: str) -> str:
    """Redact credentials from a database URL."""
    parsed = urlparse(url)
    if parsed.password:
        redacted = parsed._replace(
            netloc=f"{parsed.username}:***@{parsed.hostname}"
            + (f":{parsed.port}" if parsed.port else "")
        )
        return redacted.geturl()
    return url


def _check_db_connectivity(db_url: str) -> tuple[bool, str]:
    """Attempt a lightweight DB connectivity check.

    Returns (connected, message).
    """
    try:
        import psycopg

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True, "connected"
    except Exception as exc:  # noqa: BLE001
        logger.debug("DB connectivity check failed: %s", exc)
        return False, str(exc)


@status_app.callback(invoke_without_command=True)
def status_overview(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show system health overview."""
    if ctx.invoked_subcommand is not None:
        return

    settings = ctx.obj["settings"]
    profiles = get_all_profiles()

    # Provider health
    provider_results = []
    for name in sorted(profiles):
        profile = profiles[name]
        auth = resolve_auth(profile, settings)
        provider_results.append(
            {
                "name": profile.name,
                "auth_valid": auth.is_valid(),
                "active_source": auth.active_source,
            }
        )

    # DB connectivity — TimescaleDB only (MarketDB removed in slice 152)
    db_info: dict = {"configured": False, "connected": False}
    url = settings.timescale_db_url
    if url:
        db_info["configured"] = True
        db_info["timescale_url"] = _redact_url(str(url))
        connected, message = _check_db_connectivity(str(url))
        db_info["timescale_connected"] = connected
        if not connected:
            db_info["timescale_error"] = message
        else:
            db_info["connected"] = True

    if json_output:
        print_result(
            {"providers": provider_results, "database": db_info},
            json_mode=True,
        )
        return

    # Text output — Providers section
    table = make_table(
        "Providers",
        [("Name", "cyan"), ("Auth", "")],
    )
    for p in provider_results:
        icon = "[green]✓[/green]" if p["auth_valid"] else "[red]✗[/red]"
        source = p["active_source"] or ""
        table.add_row(p["name"], f"{icon} {source}")
    print_result(table, json_mode=False)

    # Text output — Database section
    if not db_info["configured"]:
        print_result(
            "\n[bold]Database[/bold]: [dim]not configured (MT_DB_URL)[/dim]",
            json_mode=False,
        )
    elif db_info["connected"]:
        connected_urls = ", ".join(
            db_info[f"{label}_url"]
            for label in ("market", "timescale")
            if db_info.get(f"{label}_connected")
        )
        print_result(
            f"\n[bold]Database[/bold]: [green]connected[/green] ({connected_urls})",
            json_mode=False,
        )
    else:
        errors = "; ".join(
            db_info[f"{label}_error"]
            for label in ("market", "timescale")
            if db_info.get(f"{label}_error")
        )
        print_result(
            f"\n[bold]Database[/bold]: [red]error[/red] — {errors or 'unknown'}",
            json_mode=False,
        )
