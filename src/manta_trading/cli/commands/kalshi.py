"""``mt data kalshi`` — Kalshi catalog commands (slice 262, Decision 12).

``sync`` runs :class:`~manta_trading.data.kalshi.sync.CatalogSync` over the
real client and repository; ``status`` reads the database only. Exit codes
(Decision 11) are defined here and nowhere else: the core reports a
``SyncOutcome`` and this module maps it. Slice 263 reuses both.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import typer

from manta_trading.cli.output import make_table, print_error, print_result
from manta_trading.data.kalshi.constants import (
    DB_CONNECT_TIMEOUT_SECONDS,
    KALSHI_SETTLEMENT_STUCK_AFTER,
)
from manta_trading.data.kalshi.sync_types import SyncOutcome
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    from manta_trading.config import Settings
    from manta_trading.data.kalshi.status import CatalogStatus
    from manta_trading.data.kalshi.sync_types import SyncResult

logger = get_logger(__name__)

kalshi_app = typer.Typer(
    name="kalshi",
    help="Kalshi event-contract catalog: sync and status.",
    no_args_is_help=True,
)

# Exit codes (design Decision 11). Integers live here only.
EXIT_OK = 0
EXIT_PREFLIGHT = 1
EXIT_PROVIDER = 2
EXIT_SYNC_PARTIAL = 3
EXIT_STORAGE = 4

EXIT_BY_OUTCOME: dict[SyncOutcome, int] = {
    SyncOutcome.OK: EXIT_OK,
    SyncOutcome.PARTIAL: EXIT_SYNC_PARTIAL,
    SyncOutcome.PROVIDER_ABORT: EXIT_PROVIDER,
    SyncOutcome.STORAGE_ABORT: EXIT_STORAGE,
}


def parse_settled_since(value: str) -> datetime:
    """An ISO-8601 instant with an explicit offset; naive input is rejected."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"--settled-since is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"--settled-since must carry a UTC offset (e.g. {value}Z or {value}+00:00)"
        )
    return parsed.astimezone(UTC)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


_SETTLED_SINCE_OPTION = typer.Option(
    None,
    "--settled-since",
    help="Drain the settled stream from this ISO-8601 instant (with offset) "
    "instead of the stored watermark / historical cutoff. This run only.",
)
_EVENTS_FILE_OPTION = typer.Option(
    None, "--events-file", help="Append structured run events as JSONL here."
)
_JSON_OPTION = typer.Option(False, "--json", help="Emit JSON instead of Rich text.")


@kalshi_app.command("sync")
def kalshi_sync(
    ctx: typer.Context,
    settled_since: str | None = _SETTLED_SINCE_OPTION,
    events_file: Path | None = _EVENTS_FILE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Full walk of the live catalog, the settled stream, and the awaiting set."""
    settings: Settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(EXIT_PREFLIGHT)
    since: datetime | None = None
    if settled_since is not None:
        try:
            since = parse_settled_since(settled_since)
        except ValueError as exc:
            print_error(str(exc), json_mode=json_output)
            raise typer.Exit(EXIT_PREFLIGHT) from exc
    raise typer.Exit(asyncio.run(run_sync(settings, since, events_file, json_output)))


async def run_sync(
    settings: Settings,
    settled_since: datetime | None,
    events_file: Path | None,
    json_output: bool,
) -> int:
    """Preflight, run, summarize; returns the exit code."""
    from manta_trading.data.kalshi.auth import KalshiCredentialError
    from manta_trading.data.kalshi.client import KalshiClient
    from manta_trading.data.kalshi.db import PreflightError, open_sync_connection
    from manta_trading.data.kalshi.events import JsonlSyncEventSink, NullSyncEventSink
    from manta_trading.data.kalshi.repository import CatalogRepository
    from manta_trading.data.kalshi.sync import CatalogSync, classify
    from manta_trading.providers.errors import ProviderError

    try:
        client = KalshiClient.from_settings(settings)
    except KalshiCredentialError as exc:
        print_error(str(exc), json_mode=json_output)
        return EXIT_PREFLIGHT
    try:
        conn = await open_sync_connection(str(settings.timescale_db_url))
    except PreflightError as exc:
        await client.aclose()
        print_error(str(exc), json_mode=json_output)
        return EXIT_PREFLIGHT
    sink = JsonlSyncEventSink(events_file) if events_file else NullSyncEventSink()
    sync = CatalogSync(client, CatalogRepository(conn), sink)
    failure: ProviderError | psycopg.OperationalError | None = None
    try:
        await sync.run(settled_since=settled_since)
    except ProviderError as exc:
        failure = exc
        print_error(f"provider abort: {exc}", json_mode=json_output)
    except psycopg.OperationalError as exc:
        failure = exc
        logger.exception("kalshi sync storage failure")
        print_error(f"storage abort: {exc}", json_mode=json_output)
    finally:
        await client.aclose()
        await conn.close()
        if isinstance(sink, JsonlSyncEventSink):
            sink.close()
    outcome = classify(sync.result, failure)
    print_summary(sync.result, outcome, json_output)
    return EXIT_BY_OUTCOME[outcome]


def print_summary(result: SyncResult, outcome: SyncOutcome, json_output: bool) -> None:
    from rich import print as rprint

    if json_output:
        payload: dict[str, Any] = {
            **result.to_dict(),
            "outcome": str(outcome),
            "exit_code": EXIT_BY_OUTCOME[outcome],
        }
        print_result(payload, json_mode=True)
        return
    table = make_table(
        "Kalshi catalog sync",
        [
            ("Phase", "cyan"),
            ("Fetched", ""),
            ("Written", ""),
            ("Unchanged", ""),
            ("Skipped", ""),
        ],
    )
    for phase, counts in result.phases.items():
        table.add_row(
            str(phase),
            f"{counts.fetched:,}",
            f"{counts.written:,}",
            f"{counts.unchanged:,}",
            f"{counts.skipped:,}",
        )
    print_result(table, json_mode=False)
    transitions = ", ".join(
        f"{a}→{b} {n:,}" for (a, b), n in result.transitions.items()
    )
    watermark = result.watermark_ts.isoformat() if result.watermark_ts else "unset"
    rprint(f"  transitions   {transitions or 'none'}")
    rprint(
        f"  settled       windows {result.windows_completed}  "
        f"captured {result.settled_captured:,}  watermark → {watermark}"
    )
    rprint(
        f"  awaiting      entered {result.awaiting_entered:,}  retired "
        f"{result.awaiting_retired:,}  checked {result.awaiting_checked:,}  "
        f"unreachable {result.awaiting_unreachable:,}"
    )
    rprint(
        f"  item errors   {len(result.item_errors):,}    "
        f"duration {result.duration_ms} ms    "
        f"outcome [bold]{outcome}[/bold] (exit {EXIT_BY_OUTCOME[outcome]})"
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

NEVER_SYNCED = "Kalshi catalog has never synced."


@kalshi_app.command("status")
def kalshi_status(ctx: typer.Context, json_output: bool = _JSON_OPTION) -> None:
    """Catalog counts, settlement watermark, and the awaiting-settlement set.

    Reads the database only (no API call); reports before any sync has run.
    """
    from manta_trading.data.kalshi.status import read_catalog_status

    settings: Settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(EXIT_PREFLIGHT)
    try:
        with psycopg.connect(
            str(settings.timescale_db_url), connect_timeout=DB_CONNECT_TIMEOUT_SECONDS
        ) as conn:
            status = read_catalog_status(conn)
    except psycopg.OperationalError as exc:
        print_error(f"database unreachable: {exc}", json_mode=json_output)
        raise typer.Exit(EXIT_PREFLIGHT) from exc
    if status is None:
        print_result(
            {"synced": False} if json_output else NEVER_SYNCED, json_mode=json_output
        )
        raise typer.Exit(EXIT_OK)
    if json_output:
        print_result({"synced": True, **status.to_dict()}, json_mode=True)
    else:
        print_status(status, datetime.now(UTC))
    raise typer.Exit(EXIT_OK)


def print_status(status: CatalogStatus, now: datetime) -> None:
    from rich import print as rprint

    from manta_trading.data.kalshi.status import age_bucket_labels

    awaiting = status.awaiting
    by_status = " · ".join(
        f"{s.value} {n:,}" for s, n in status.markets_by_status.items()
    )
    histogram = " · ".join(
        f"{label} {n:,}"
        for label, n in zip(age_bucket_labels(), awaiting.age_histogram, strict=True)
    )
    oldest = (
        f"{awaiting.oldest_ticker} ({awaiting.oldest_age.days:,} d)"
        if awaiting.oldest_ticker and awaiting.oldest_age is not None
        else "none"
    )
    rprint("[bold]Kalshi catalog[/bold]")
    rprint(f"  last full sync      {_when(status.last_full_sync_at, now)}")
    rprint(f"  settled watermark   {_when(status.watermark_ts, now)}")
    rprint(f"  series / events     {status.series:,} / {status.events:,}")
    rprint(f"[bold]Markets by status[/bold]     {by_status}")
    rprint(f"[bold]Awaiting settlement[/bold]   {awaiting.total:,} markets")
    rprint(f"  age                 {histogram}")
    rprint(
        f"  past {KALSHI_SETTLEMENT_STUCK_AFTER.days}d threshold   "
        f"{awaiting.past_threshold:,}   oldest {oldest}"
    )
    rprint(
        f"  checked directly    {awaiting.checked_directly:,}  "
        "(looked up by ticker; still unsettled)"
    )


def _when(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "never"
    minutes = int((now - value).total_seconds() // 60)
    return f"{value:%Y-%m-%d %H:%M:%S} UTC  ({minutes:,} min ago)"
