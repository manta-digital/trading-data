"""``mt data accounting`` — the minute universe from the calendar (2026-09-11).

Prints, per year and in total, how many symbol-sessions should exist, how
many hold bars, and how the rest splits between untraded sessions and the
gap table's word on the traded ones. See ``data.gaps.minute_accounting``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

import psycopg
import typer

from manta_trading.cli.commands.health import EXIT_UNAVAILABLE
from manta_trading.cli.output import print_error, print_result
from manta_trading.data.gaps.minute_accounting import (
    compute_minute_accounting,
    render_minute_accounting,
    summary_line,
)
from manta_trading.data.kalshi.constants import DB_CONNECT_TIMEOUT_SECONDS


def data_accounting(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Account for the minute universe: expected vs covered symbol-sessions by year.

    Several minutes against the production database. Exit 2 when it could
    not run.
    """
    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL must be configured.", json_mode=json_output)
        raise typer.Exit(EXIT_UNAVAILABLE)
    try:
        with psycopg.connect(
            str(settings.timescale_db_url),
            connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
        ) as conn:
            rows = compute_minute_accounting(conn, now=datetime.now(UTC))
    except psycopg.OperationalError as exc:
        print_error(f"accounting could not run: {exc}", json_mode=json_output)
        raise typer.Exit(EXIT_UNAVAILABLE) from exc

    if json_output:
        print_result(
            {"rows": [asdict(r) for r in rows], "summary": summary_line(rows)},
            json_mode=True,
        )
        return
    typer.echo(render_minute_accounting(rows))
    typer.echo(summary_line(rows))
