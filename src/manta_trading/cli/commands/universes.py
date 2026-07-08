"""CLI commands for index constituent tracking — `mt data universes` (slice 161).

Commands:
  ls       — Show tracked universes with active member count and last-refresh date.
  as-of    — List members of a universe as of a given date.
  refresh  — Fetch the latest SP500 CSV from GitHub and apply new rows.
"""

from __future__ import annotations

import json
import sys
from datetime import date

import httpx
import psycopg
import typer
from rich.console import Console
from rich.table import Table

from manta_trading.cli.output import print_error
from manta_trading.data.universe.constants import (
    SP500_CSV_URL,
    SP500_GITHUB_API_URL,
    TRACKED_UNIVERSES,
)

universes_app = typer.Typer(
    name="universes",
    help="Index constituent tracking (SP500 historical membership).",
)


def _require_db_url(ctx: typer.Context, json_output: bool) -> str:
    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)
    return str(settings.timescale_db_url)


@universes_app.command("ls")
def universes_ls(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show tracked universes with active member count and last-refresh date."""
    db_url = _require_db_url(ctx, json_output)

    sql = """
        SELECT
            universe_name,
            COUNT(*) FILTER (WHERE removed_date IS NULL) AS active_members,
            MAX(added_date) AS last_refresh
        FROM universe_members
        GROUP BY universe_name
        ORDER BY universe_name
    """
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
    except Exception as exc:
        print_error(f"DB query failed: {exc}", json_mode=json_output)
        raise typer.Exit(1) from exc

    if json_output:
        data = [
            {"universe": r[0], "members": r[1], "last_refresh": str(r[2]) if r[2] else None}
            for r in rows
        ]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
        return

    console = Console()
    table = Table(title="Tracked Universes")
    table.add_column("Universe", style="cyan")
    table.add_column("Members", style="green", justify="right")
    table.add_column("Last Refresh", style="yellow")
    for row in rows:
        table.add_row(row[0], str(row[1]), str(row[2]) if row[2] else "—")
    console.print(table)


@universes_app.command("as-of")
def universes_as_of(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Universe name (e.g. sp500)"),
    as_of_date: str = typer.Option(..., "--date", help="Date in YYYY-MM-DD format"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List members of a universe as of a given date."""
    db_url = _require_db_url(ctx, json_output)

    if name not in TRACKED_UNIVERSES:
        print_error(
            f"Unknown universe '{name}'. Known: {', '.join(sorted(TRACKED_UNIVERSES))}",
            json_mode=json_output,
        )
        raise typer.Exit(1)

    try:
        parsed_date = date.fromisoformat(as_of_date)
    except ValueError:
        print_error(f"Invalid date '{as_of_date}', expected YYYY-MM-DD.", json_mode=json_output)
        raise typer.Exit(1)

    sql = """
        SELECT symbol FROM universe_members
        WHERE universe_name = %s
          AND added_date <= %s
          AND (removed_date IS NULL OR removed_date > %s)
        ORDER BY symbol
    """
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (name, parsed_date, parsed_date))
                symbols = [row[0] for row in cur.fetchall()]
    except Exception as exc:
        print_error(f"DB query failed: {exc}", json_mode=json_output)
        raise typer.Exit(1) from exc

    if json_output:
        sys.stdout.write(json.dumps(symbols, indent=2) + "\n")
        return

    for symbol in symbols:
        typer.echo(symbol)


def _fetch_sp500_csv() -> str:
    """Fetch the best available SP500 historical CSV from GitHub.

    First tries the GitHub API to find the latest dated historical file
    (back to 1996). Falls back to the stable URL (2019-onward) if the API
    call fails or returns no matching file.
    """
    import re

    try:
        api_resp = httpx.get(SP500_GITHUB_API_URL, timeout=15.0, follow_redirects=True)
        api_resp.raise_for_status()
        entries = api_resp.json()
        pattern = re.compile(r"S&P 500 Historical Components & Changes\(\d{2}-\d{2}-\d{4}\)\.csv")
        matches = [e["download_url"] for e in entries if pattern.match(e.get("name", ""))]
        if matches:
            # Sort by filename date descending; filenames contain MM-DD-YYYY.
            matches.sort(reverse=True)
            resp = httpx.get(matches[0], timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            return resp.text
    except Exception:
        pass  # Fall through to stable URL

    resp = httpx.get(SP500_CSV_URL, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


@universes_app.command("refresh")
def universes_refresh(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-row progress"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Fetch the SP500 historical CSV from GitHub and apply any new rows.

    Idempotent: rows already loaded are skipped. Safe to re-run at any time.
    """
    from manta_trading.data.universe.tracking import import_sp500_csv

    db_url = _require_db_url(ctx, json_output)

    try:
        csv_text = _fetch_sp500_csv()
    except Exception as exc:
        print_error(f"Failed to fetch SP500 CSV: {exc}", json_mode=json_output)
        raise typer.Exit(1) from exc

    def on_progress(done: int, total: int, change_date: date) -> None:
        if verbose and not json_output:
            typer.echo(f"  [{done+1}/{total}] {change_date}", err=True)

    try:
        with psycopg.connect(db_url) as conn:
            imported, skipped = import_sp500_csv(conn, csv_text, on_progress=on_progress)
    except Exception as exc:
        print_error(f"Import failed: {exc}", json_mode=json_output)
        raise typer.Exit(1) from exc

    if json_output:
        sys.stdout.write(json.dumps({"imported": imported, "skipped": skipped}) + "\n")
        return

    typer.echo(f"sp500: {imported} change-rows imported, {skipped} already up-to-date")
