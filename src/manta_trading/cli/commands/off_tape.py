"""``mt data off-tape``: mark minute history with off-tape masks (slice 171).

``sweep`` rewrites every chunk that still holds an unevaluated (``NULL``)
mark; see ``market/maintenance/off_tape_sweep.py``. The maintenance URL is a
required argument and never read from the environment inside the tool.

Exit codes: 0 every pending chunk swept (the newest may be deferred while a
minute pass runs); 1 a chunk stayed locked after its retry, or the sweep
stopped on a checksum mismatch.
"""

from __future__ import annotations

import psycopg
import typer

from manta_trading.market.maintenance.off_tape_sweep import (
    ChecksumMismatch,
    list_chunks,
    plan,
    run_sweep,
)

off_tape_app = typer.Typer(
    name="off-tape",
    help="Off-tape print marks on minute bars (slice 171).",
    no_args_is_help=True,
)


def _connect(url: str) -> psycopg.Connection:
    conn = psycopg.connect(url, autocommit=True)
    conn.execute("SET timezone = 'UTC'")
    # One chunk rewrite can outlast a role's statement_timeout; waits are
    # bounded by OFF_TAPE_SWEEP_LOCK_TIMEOUT inside each chunk transaction.
    conn.execute("SET statement_timeout = 0")
    return conn


def _dry_run(conn: psycopg.Connection) -> None:
    chunks = list_chunks(conn)
    swept = plan(conn)
    compressed = sum(chunk.compressed for chunk in swept.pending)
    typer.echo(
        f"chunks: {len(chunks)} total, {len(swept.pending)} to sweep "
        f"({compressed} compressed, {len(swept.pending) - compressed} uncompressed)"
    )
    if swept.deferred is not None:
        typer.echo(f"deferred while a minute pass runs: {swept.deferred.name}")


@off_tape_app.command("sweep")
def sweep(
    maintenance_url: str = typer.Option(
        ..., "--maintenance-url", help="Maintenance (DDL) URL of the primary database."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report chunks remaining; write nothing."
    ),
) -> None:
    """Mark every minute chunk that still holds unevaluated bars, oldest first."""
    with _connect(maintenance_url) as conn:
        if dry_run:
            _dry_run(conn)
            return
        try:
            report = run_sweep(conn)
        except ChecksumMismatch as exc:
            typer.echo(f"stopped: {exc}; chunk rolled back", err=True)
            raise typer.Exit(1) from exc
    typer.echo(f"swept {len(report.done)} chunk(s)")
    if report.deferred is not None:
        typer.echo(f"deferred while a minute pass runs: {report.deferred}")
    if report.unlocked:
        typer.echo(f"still locked after retry: {', '.join(report.unlocked)}", err=True)
        raise typer.Exit(1)
