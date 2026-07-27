"""data subcommand — data acquisition and management."""

from __future__ import annotations

import asyncio
from datetime import date as _date_t
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from manta_trading.cli.output import make_table, print_error, print_result
from manta_trading.logging import get_logger
from manta_trading.providers.auth import resolve_auth
from manta_trading.providers.profiles import get_profile

if TYPE_CHECKING:
    from manta_trading.constants import Granularity

logger = get_logger(__name__)

data_app = typer.Typer(
    name="data",
    help="Data acquisition and management.",
    no_args_is_help=True,
)

instruments_app = typer.Typer(
    name="instruments",
    help="Manage instrument registry.",
    no_args_is_help=True,
)

calendars_app = typer.Typer(
    name="calendars",
    help="Trading calendar information.",
    no_args_is_help=True,
)

migrate_app = typer.Typer(
    name="migrate",
    help="Schema migration commands.",
    no_args_is_help=True,
)

daemon_app = typer.Typer(
    name="daemon",
    help="Long-running data acquisition daemon.",
    no_args_is_help=True,
)

lists_app = typer.Typer(
    name="lists",
    help="Named symbol lists (config/symbol-lists.yaml).",
    no_args_is_help=True,
)

ca_app = typer.Typer(
    name="ca",
    help="Corporate-actions commands (splits + dividends).",
    no_args_is_help=True,
)

caggs_app = typer.Typer(
    name="caggs",
    help="Continuous aggregate management (refresh, status, verify, repair).",
    no_args_is_help=True,
)

from manta_trading.cli.commands.universes import universes_app

data_app.add_typer(daemon_app, name="daemon")
data_app.add_typer(instruments_app, name="instruments")
data_app.add_typer(calendars_app, name="calendars")
data_app.add_typer(migrate_app, name="migrate")
data_app.add_typer(lists_app, name="lists")
data_app.add_typer(ca_app, name="ca")
data_app.add_typer(caggs_app, name="caggs")
data_app.add_typer(universes_app, name="universes")


_DEFAULT_LISTS_CONFIG: Path = Path("config/symbol-lists.yaml")
"""Project-relative path to symbol-lists config; resolved against cwd."""


# ---------------------------------------------------------------------------
# Top-level data commands
# ---------------------------------------------------------------------------

_EXIT_PREFLIGHT_FAILED: int = 1


@data_app.command("init")
def data_init(
    ctx: typer.Context,
    validate_only: bool = typer.Option(
        False,
        "--validate-only",
        help="Show migration state without applying anything.",
    ),
    yes: bool = typer.Option(  # noqa: ARG001 - reserved for future destructive ops
        False,
        "--yes",
        "-y",
        help="Reserved for future destructive operations; currently a no-op.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Initialize a TimescaleDB database for cold-start (slice 156).

    Applies all pending schema migrations to bring an empty database up to
    the current schema. Idempotent: safe to re-run on a healthy DB. Replaces
    the deleted ``python -m manta_trading.market.timescale_init`` invocation.
    """
    from rich.console import Console
    from rich.table import Table

    settings = ctx.obj["settings"]

    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)

    db = _create_timescale_db(ctx)
    try:
        if validate_only:
            state = db.list_migration_state()
        else:
            applied = db.apply_schema_migrations()
            state = db.list_migration_state()
    finally:
        db.close()

    if validate_only:
        applied_ids: list[str] = [e["id"] for e in state.get("applied", [])]
        pending_ids: list[str] = [e["id"] for e in state.get("pending", [])]
        if json_output:
            print_result(
                {
                    "validate_only": True,
                    "applied": applied_ids,
                    "pending": pending_ids,
                },
                json_mode=True,
            )
            return
        console = Console()
        table = Table(title="data init — validate only")
        table.add_column("Metric", style="bold")
        table.add_column("Count")
        table.add_row("Applied", str(len(applied_ids)))
        table.add_row("Pending", str(len(pending_ids)))
        console.print(table)
        return

    if json_output:
        print_result(
            {
                "applied_now": applied,
                "applied_total": len(state.get("applied", [])),
                "pending_remaining": len(state.get("pending", [])),
            },
            json_mode=True,
        )
        return

    console = Console()
    table = Table(title="data init")
    table.add_column("Metric", style="bold")
    table.add_column("Count")
    table.add_row("Applied this run", str(len(applied)))
    table.add_row("Total applied", str(len(state.get("applied", []))))
    table.add_row("Pending remaining", str(len(state.get("pending", []))))
    console.print(table)


@migrate_app.command("apply")
def migrate_apply(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Apply pending schema migrations."""
    settings = ctx.obj["settings"]

    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)

    db = _create_timescale_db(ctx)
    try:
        applied = db.apply_schema_migrations()
    finally:
        db.close()

    if json_output:
        print_result({"applied": applied}, json_mode=True)
        return

    for mid in applied:
        print_result(f"Applied: {mid}", json_mode=False)
    print_result(f"{len(applied)} migration(s) applied", json_mode=False)


@migrate_app.command("status")
def migrate_status(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show applied and pending migrations."""
    from rich.console import Console
    from rich.table import Table

    settings = ctx.obj["settings"]

    if not settings.timescale_db_url:
        if json_output:
            print_result({"connected": False, "error": "URL not configured", "applied": [], "pending": []}, json_mode=True)
        else:
            print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=False)
        raise typer.Exit(1)

    try:
        db = _create_timescale_db(ctx)
        try:
            state = db.list_migration_state()
        finally:
            db.close()
    except Exception as exc:
        if json_output:
            print_result({"connected": False, "error": str(exc), "applied": [], "pending": []}, json_mode=True)
        else:
            print_error(f"Could not connect: {exc}", json_mode=False)
        raise typer.Exit(1)

    if json_output:
        print_result({"connected": True, **state}, json_mode=True)
        return

    console = Console()
    table = Table(title="migrations")
    table.add_column("ID", style="bold")
    table.add_column("Status")
    table.add_column("Description")
    table.add_column("Applied At")

    for entry in state.get("applied", []):
        table.add_row(
            entry["id"],
            "[green]applied[/green]",
            entry.get("description", ""),
            entry.get("applied_at") or "—",
        )
    for entry in state.get("pending", []):
        table.add_row(
            entry["id"],
            "[yellow]pending[/yellow]",
            entry.get("description", ""),
            "—",
        )
    console.print(table)

    applied_n = len(state.get("applied", []))
    pending_n = len(state.get("pending", []))
    console.print(f"{applied_n} applied, {pending_n} pending")


def _validate_credentials(ctx: typer.Context, json_output: bool) -> str | None:
    """Validate EODHD credentials (sole provider after slice 152)."""
    settings = ctx.obj["settings"]
    if not settings.eodhd_api_key:
        print_error(
            "MT_EODHD_API_KEY not configured.",
            json_mode=json_output,
        )
        raise typer.Exit(1)
    return settings.eodhd_api_key


def _create_timescale_db(ctx: typer.Context):
    """Create TimescaleMinuteDataDB from settings timescale_db_url."""
    from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error(
            "MT_TIMESCALE_DB_URL not configured. "
            "Set the environment variable or add it to your .env file.",
            json_mode=False,
        )
        raise typer.Exit(1)

    return TimescaleMinuteDataDB(conninfo=settings.timescale_db_url)


# ---------------------------------------------------------------------------
# Instrument registry commands
# ---------------------------------------------------------------------------


def _create_instrument_registry(ctx: typer.Context):
    """Create InstrumentRegistry from settings timescale_db_url."""
    from manta_trading.data.base.instrument_registry import InstrumentRegistry

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error(
            "MT_TIMESCALE_DB_URL not configured. "
            "Set the environment variable or add it to your .env file.",
            json_mode=False,
        )
        raise typer.Exit(1)

    return InstrumentRegistry(conninfo=settings.timescale_db_url)


@instruments_app.command("list")
def instruments_list(
    ctx: typer.Context,
    venue: str | None = typer.Option(None, "--venue", help="Filter by venue"),
    asset_class: str | None = typer.Option(
        None, "--asset-class", help="Filter by asset class"
    ),
    inactive: bool = typer.Option(
        False, "--inactive", help="Include inactive instruments"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List registered instruments."""
    registry = _create_instrument_registry(ctx)
    try:
        instruments = registry.list_instruments(
            venue=venue,
            asset_class=asset_class,
            active_only=not inactive,
        )

        if json_output:
            data = [
                {
                    "instrument_id": i.instrument_id,
                    "symbol": i.symbol,
                    "canonical_id": i.canonical_id,
                    "venue": i.venue,
                    "asset_class": i.asset_class,
                    "listed": not i.delisted_at_eodhd and i.delisted_date is None,
                }
                for i in instruments
            ]
            print_result(data, json_mode=True)
            return

        table = make_table(
            "Instruments",
            [
                ("Symbol", "bold"),
                ("Canonical ID", ""),
                ("Venue", ""),
                ("Asset Class", ""),
                ("Listed", ""),
            ],
        )
        for inst in instruments:
            table.add_row(
                inst.symbol,
                inst.canonical_id,
                inst.venue,
                inst.asset_class,
                "yes" if (not inst.delisted_at_eodhd and inst.delisted_date is None) else "no",
            )
        print_result(table, json_mode=False)
        print_result(f"\n{len(instruments)} instrument(s)", json_mode=False)

    finally:
        registry.close()


# ---------------------------------------------------------------------------
# Calendar commands
# ---------------------------------------------------------------------------


def _get_timescale_url(ctx: typer.Context) -> str:
    """Return timescale_db_url from settings, failing explicitly if missing."""
    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error(
            "MT_TIMESCALE_DB_URL not configured. "
            "Set the environment variable or add it to your .env file.",
            json_mode=False,
        )
        raise typer.Exit(1)
    return settings.timescale_db_url


@instruments_app.command("rebuild")
def instruments_rebuild(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview counts without DB mutation"
    ),
    skip_finnhub: bool = typer.Option(
        False, "--skip-finnhub", help="Skip Finnhub IPO-date enrichment loop"
    ),
    only_finnhub: bool = typer.Option(
        False, "--only-finnhub", help="Run only the Finnhub enrichment step; skip all EODHD steps"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON summary"),
) -> None:
    """Rebuild instruments registry from EODHD bulk symbol-list (slice 141)."""
    from manta_trading.data.universe.eodhd_symbol_list_client import EodhdAccessError
    from manta_trading.data.universe.rebuild import run_rebuild

    settings = ctx.obj["settings"]

    if not settings.timescale_db_url:
        print_error(
            "MT_TIMESCALE_DB_URL not configured", json_mode=json_output
        )
        raise typer.Exit(1)

    finnhub_key = settings.finnhub_api_key or ""
    if not only_finnhub:
        if not settings.eodhd_api_key:
            print_error(
                "MT_EODHD_API_KEY not configured", json_mode=json_output
            )
            raise typer.Exit(1)
        if not skip_finnhub and not finnhub_key:
            print_error(
                "MT_FINNHUB_API_KEY not configured; pass --skip-finnhub to proceed without enrichment",
                json_mode=json_output,
            )
            raise typer.Exit(1)
    else:
        if not finnhub_key:
            print_error(
                "MT_FINNHUB_API_KEY not configured", json_mode=json_output
            )
            raise typer.Exit(1)

    try:
        summary = asyncio.run(
            run_rebuild(
                db_url=settings.timescale_db_url,
                dry_run=dry_run,
                skip_finnhub=skip_finnhub,
                only_finnhub=only_finnhub,
                eodhd_api_key=settings.eodhd_api_key or "",
                finnhub_api_key=finnhub_key,
            )
        )
    except EodhdAccessError as exc:
        print_error(f"EODHD access error: {exc}", json_mode=json_output)
        raise typer.Exit(1)

    if json_output:
        print_result(summary, json_mode=True)
        return

    table = make_table(
        "Universe Rebuild Summary",
        [("Metric", ""), ("Count", "")],
    )
    for key in (
        "inserted", "updated", "unchanged", "orphans_deleted",
        "finnhub_populated", "finnhub_not_found", "finnhub_errors",
        "non_us_dropped",
    ):
        table.add_row(key, str(summary.get(key, 0)))
    print_result(table, json_mode=False)
    if dry_run:
        print_result(f"\n[dry-run] would_process: {summary.get('would_process', 0)}", json_mode=False)


@instruments_app.command("populate-delisted-dates")
def instruments_populate_delisted_dates(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch and parse but skip DB writes."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print per-symbol progress."),
) -> None:
    """Populate delisted_date for all delisted instruments via EODHD last-bar fetch.

    Fetches 1 EOD bar per symbol (1 credit each) for every instrument where
    delisted_at_eodhd=true and delisted_date IS NULL.
    """
    import httpx
    import psycopg

    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR
    from manta_trading.data.acquisition.quota import QuotaBucket
    from manta_trading.data.universe.populate_delisted_dates import populate_delisted_dates

    settings = ctx.obj["settings"]

    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=False)
        raise typer.Exit(1)

    api_key = _validate_credentials(ctx, False)
    if api_key is None:
        raise typer.Exit(1)

    bucket = QuotaBucket()
    token = QUOTA_BUCKET_VAR.set(bucket)

    def _on_progress(processed: int, total: int, sym: str, last_bar_date) -> None:
        if not verbose:
            return
        if last_bar_date is None:
            typer.echo(f"{sym}: EMPTY")
        else:
            typer.echo(f"{sym}: {last_bar_date}")

    try:
        with (
            psycopg.connect(str(settings.timescale_db_url)) as conn,
            httpx.Client(timeout=30.0) as http,
        ):
            report = populate_delisted_dates(
                conn,
                http,
                api_key=api_key,
                dry_run=dry_run,
                on_progress=_on_progress,
            )
    finally:
        QUOTA_BUCKET_VAR.reset(token)

    if dry_run:
        typer.echo(
            f"DRY RUN — would update={report.total - report.skipped_empty - report.error_count} "
            f"skipped_empty={report.skipped_empty} errors={report.error_count}"
        )
    else:
        typer.echo(
            f"Done. updated={report.updated} skipped_empty={report.skipped_empty} "
            f"errors={report.error_count}"
        )

    if report.error_count > 0:
        raise typer.Exit(code=1)


@calendars_app.command("list")
def calendars_list(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show registered trading calendars."""
    import psycopg
    from psycopg.rows import dict_row as psycopg_dict_row

    conninfo = _get_timescale_url(ctx)

    with psycopg.connect(conninfo) as conn:
        with conn.cursor(row_factory=psycopg_dict_row) as cur:
            cur.execute(
                "SELECT calendar_id, calendar_name, timezone,"
                "  market_open_time, market_close_time,"
                "  has_extended_hours, extended_open_time, extended_close_time "
                "FROM trading_calendars ORDER BY calendar_id"
            )
            rows = cur.fetchall()

    if json_output:
        data = [
            {
                "calendar_id": r["calendar_id"],
                "calendar_name": r["calendar_name"],
                "timezone": r["timezone"],
                "market_open": str(r["market_open_time"]),
                "market_close": str(r["market_close_time"]),
                "has_extended_hours": r["has_extended_hours"],
                "extended_open": str(r["extended_open_time"]) if r["extended_open_time"] else None,
                "extended_close": str(r["extended_close_time"]) if r["extended_close_time"] else None,
            }
            for r in rows
        ]
        print_result(data, json_mode=True)
        return

    table = make_table(
        "Trading Calendars",
        [
            ("Calendar ID", "bold"),
            ("Name", ""),
            ("Timezone", ""),
            ("Market Hours", ""),
            ("ETH Hours", ""),
            ("ETH", ""),
        ],
    )
    for r in rows:
        market_hours = f"{r['market_open_time']}-{r['market_close_time']}"
        eth_hours = (
            f"{r['extended_open_time']}-{r['extended_close_time']}"
            if r["has_extended_hours"]
            else "N/A"
        )
        table.add_row(
            r["calendar_id"],
            r["calendar_name"],
            r["timezone"],
            market_hours,
            eth_hours,
            "yes" if r["has_extended_hours"] else "no",
        )
    print_result(table, json_mode=False)
    print_result(f"\n{len(rows)} calendar(s)", json_mode=False)


@calendars_app.command("holidays")
def calendars_holidays(
    ctx: typer.Context,
    calendar: str = typer.Option(..., "--calendar", help="Calendar ID (e.g. NYSE)"),
    year: int = typer.Option(
        None, "--year", help="Year (default: current year)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show holidays for a trading calendar."""
    from datetime import date as date_cls

    from manta_trading.data.base.trading_calendar import TradingCalendar

    if year is None:
        year = date_cls.today().year

    conninfo = _get_timescale_url(ctx)
    cal = TradingCalendar(calendar, conninfo)
    try:
        holidays = cal.get_holidays(year)

        if json_output:
            data = [
                {
                    "date": str(h.holiday_date),
                    "name": h.holiday_name,
                    "market_status": h.market_status.value,
                    "early_close_time": str(h.early_close_time) if h.early_close_time else None,
                    "late_open_time": str(h.late_open_time) if h.late_open_time else None,
                }
                for h in holidays
            ]
            print_result(data, json_mode=True)
            return

        table = make_table(
            f"{calendar} Holidays ({year})",
            [
                ("Date", "bold"),
                ("Holiday", ""),
                ("Status", ""),
                ("Early Close", ""),
                ("Late Open", ""),
            ],
        )
        for h in holidays:
            table.add_row(
                str(h.holiday_date),
                h.holiday_name,
                h.market_status.value,
                str(h.early_close_time) if h.early_close_time else "-",
                str(h.late_open_time) if h.late_open_time else "-",
            )
        print_result(table, json_mode=False)
        print_result(f"\n{len(holidays)} holiday(s)", json_mode=False)

    finally:
        cal.close()


# ---------------------------------------------------------------------------
# mt data status — slice 147
# ---------------------------------------------------------------------------

_VALID_HEALTH_VALUES = {"OK", "GAPS", "STALE", "FAILED"}


@data_app.command("status")
def data_status(
    ctx: typer.Context,
    symbol: str | None = typer.Option(
        None, "--symbol", help="Show detail view for a single symbol."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit JSON instead of Rich table."
    ),
    health_opt: str = typer.Option(
        "GAPS,STALE,FAILED",
        "--health",
        help="Comma-separated health values to show (OK,GAPS,STALE,FAILED).",
    ),
    daily: bool = typer.Option(False, "--daily", help="Show daily rows only."),
    minute: bool = typer.Option(False, "--minute", help="Show minute rows only."),
    all_rows: bool = typer.Option(
        False, "--all", help="Show all rows including OK (overrides --health)."
    ),
) -> None:
    """Show health of every (symbol, granularity) pair in the registry.

    Results are grouped into separate tables: daily first, then minute.
    Default: non-OK rows only. Use --daily or --minute to limit granularity.
    Use --symbol for detail + gap listing. Use --json for machine-readable output.
    """
    from rich.console import Console

    from manta_trading.cli.rendering.status_table import (
        HealthStatus,
        StatusReport,
        render_auto_extend_notice,
        render_coverage_notice,
        render_status_detail,
        render_status_footer,
        render_status_summary,
        status_report_to_json,
    )
    from manta_trading.data.maintenance.auto_extend import maybe_extend_trading_sessions
    from manta_trading.data.maintenance.status_queries import (
        fetch_all_health_counts_with_freshness,
        fetch_status_rows,
        fetch_status_rows_with_freshness,
        fetch_symbol_gaps,
    )

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(_EXIT_PREFLIGHT_FAILED)

    # Resolve health filter.
    if all_rows:
        health_filter: list[str] | None = None
    else:
        raw_health = [h.strip().upper() for h in health_opt.split(",") if h.strip()]
        invalid = [h for h in raw_health if h not in _VALID_HEALTH_VALUES]
        if invalid:
            print_error(
                f"Invalid health values: {', '.join(invalid)}. "
                f"Valid: {', '.join(sorted(_VALID_HEALTH_VALUES))}",
                json_mode=json_output,
            )
            raise typer.BadParameter(f"Invalid health: {invalid}")
        health_filter = raw_health

    # Resolve granularity filter from --daily / --minute flags.
    if daily and minute:
        granularity_filter: str | None = None  # both → no filter
    elif daily:
        granularity_filter = "daily"
    elif minute:
        granularity_filter = "minute"
    else:
        granularity_filter = None  # default: both

    import psycopg as _psycopg

    def _conn_factory():
        return _psycopg.connect(settings.timescale_db_url)

    # Auto-extend fires on every status invocation (bypass_gate — cheap MAX query).
    auto_result = maybe_extend_trading_sessions(_conn_factory, bypass_gate=True)

    with _conn_factory() as conn:
        # Freshness comes from the row fetch; the health-count fetch re-asserts
        # against slice 168's TTL verdict cache, so one guard result describes
        # both and the second probe stays cheap.
        status_rows, coverage = fetch_status_rows_with_freshness(
            conn,
            symbol=symbol,
            health_filter=health_filter,
            granularity=granularity_filter,
        )
        health_counts, _ = fetch_all_health_counts_with_freshness(conn)
        gaps = fetch_symbol_gaps(conn, symbol) if symbol else []


    # A no-row result is exactly when a stale-coverage verdict matters most: it
    # may be *why* the rows look absent. Both empty paths carry it rather than
    # reporting "no data" as though it were established fact.
    def _emit_empty(msg: str, **extra: object) -> None:
        if json_output:
            import json as _json

            payload: dict[str, object] = {"message": msg, **extra}
            if coverage is not None:
                payload["coverage_stale"] = coverage.is_stale
            print(_json.dumps(payload))
            return
        notice = render_coverage_notice(coverage)
        if notice:
            Console().print(notice)
        print_result(msg, json_mode=False)

    # Empty-universe path.
    if not status_rows and symbol is None:
        _emit_empty(
            "No instruments found. Run `mt data instruments rebuild` to populate the registry."
        )
        raise typer.Exit(0)

    # Unknown symbol / no-match path.
    if not status_rows and symbol is not None:
        # Check whether the symbol exists at all (unfiltered) to give a precise message.
        with _conn_factory() as conn:
            unfiltered = fetch_status_rows(conn, symbol=symbol, health_filter=None, granularity=None)
        if unfiltered:
            # Symbol exists but no rows match the current health/granularity filter.
            applied = []
            if health_filter:
                applied.append(f"health={','.join(health_filter)}")
            if granularity_filter:
                applied.append(f"granularity={granularity_filter}")
            filter_desc = " and ".join(applied) or "current filters"
            msg = f"No rows for {symbol} matching {filter_desc}."
        else:
            msg = (
                f"No data_status row for {symbol}. "
                "Is the symbol in the instruments registry?"
            )
        _emit_empty(msg, symbol=symbol)
        raise typer.Exit(0)

    report = StatusReport(
        scope="symbol" if symbol else "all",
        symbol=symbol,
        rows=status_rows,
        gaps=gaps,
        auto_extend=auto_result,
        summary=health_counts,
        coverage=coverage,
    )

    if json_output:
        print(status_report_to_json(report))
        return

    console = Console()

    # Above the tables, not in the footer: stale coverage understates the very
    # numbers below it, so it has to be read before them. Reports, never
    # refuses (slice 167 D3a) — exit code stays 0.
    coverage_notice = render_coverage_notice(coverage)
    if coverage_notice:
        console.print(coverage_notice)

    if symbol:
        for renderable in render_status_detail(report):
            console.print(renderable)
    else:
        for tbl in render_status_summary(report):
            console.print(tbl)

    console.print(render_status_footer(report, all_rows=all_rows))

    notice = render_auto_extend_notice(auto_result)
    if notice:
        console.print(notice)


# ---------------------------------------------------------------------------
# Horizon maintenance command (slice 144)
# ---------------------------------------------------------------------------

_EXIT_HORIZON_WARN: int = 4


@data_app.command("extend")
def data_extend(
    ctx: typer.Context,
    calendar: str | None = typer.Option(
        None,
        "--calendar",
        help=(
            "Calendar ID to extend (e.g. NYSE). "
            "Defaults to all calendars in trading_calendars."
        ),
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help=(
            "Exit non-zero if any calendar's MAX(session_date) is within "
            "TRADING_SESSIONS_HORIZON_WARN_DAYS days of today after extension."
        ),
    ),
) -> None:
    """Extend the trading_sessions horizon for one or all calendars.

    Populates trading_sessions rows from MAX(session_date)+1 through
    current_year + TRADING_SESSIONS_EXTENSION_YEARS. Idempotent: re-running
    a fully extended calendar reports 0 inserted / 0 updated.

    Exit codes:
      0   success (horizon healthy, or strict not requested)
      1   MT_TIMESCALE_URL not configured
      4   --strict: one or more calendars has horizon < today + 90 days
    """
    from datetime import date, datetime, timedelta

    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from manta_trading.constants import (
        TRADING_SESSIONS_EXTENSION_YEARS,
        TRADING_SESSIONS_HORIZON_WARN_DAYS,
    )
    from manta_trading.data.base.session_population import populate_trading_sessions

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_URL not configured.", json_mode=False)
        raise typer.Exit(_EXIT_PREFLIGHT_FAILED)

    current_year = datetime.now().year
    end_year = current_year + TRADING_SESSIONS_EXTENSION_YEARS
    today = date.today()
    warn_cutoff = today + timedelta(days=TRADING_SESSIONS_HORIZON_WARN_DAYS)

    total_inserted = 0
    horizon_warnings: list[str] = []

    with ConnectionPool(settings.timescale_db_url, min_size=1, max_size=2, open=True) as pool:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if calendar:
                    cur.execute(
                        "SELECT calendar_id, timezone, market_open, market_close "
                        "FROM trading_calendars WHERE calendar_id = %s",
                        (calendar,),
                    )
                else:
                    cur.execute(
                        "SELECT calendar_id, timezone, market_open, market_close "
                        "FROM trading_calendars"
                    )
                calendars = cur.fetchall()

        if not calendars:
            label = f"'{calendar}'" if calendar else "any"
            print_error(f"No calendar found matching {label}.", json_mode=False)
            raise typer.Exit(_EXIT_PREFLIGHT_FAILED)

        for cal_row in calendars:
            cal_id: str = cal_row["calendar_id"]

            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT MAX(session_date) AS max_date "
                        "FROM trading_sessions WHERE calendar_id = %s",
                        (cal_id,),
                    )
                    max_row = cur.fetchone()
                    max_date = max_row["max_date"] if max_row else None

                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT holiday_date, market_status, "
                        "       early_close_time, late_open_time "
                        "FROM trading_holidays WHERE calendar_id = %s",
                        (cal_id,),
                    )
                    holidays = cur.fetchall()

            start_date = (max_date + timedelta(days=1)) if max_date else date(current_year, 1, 1)
            end_date = date(end_year, 12, 31)

            if start_date <= end_date:
                calendars_row = {
                    "timezone": cal_row["timezone"],
                    "market_open": cal_row["market_open"],
                    "market_close": cal_row["market_close"],
                }
                holidays_rows = [
                    {
                        "holiday_date": h["holiday_date"],
                        "market_status": h["market_status"],
                        "early_close_time": h["early_close_time"],
                        "late_open_time": h["late_open_time"],
                    }
                    for h in holidays
                ]
                rows = populate_trading_sessions(
                    cal_id, start_date, end_date, calendars_row, holidays_rows
                )

                if rows:
                    with pool.connection() as conn:
                        with conn.cursor() as cur:
                            cur.executemany(
                                """
                                INSERT INTO trading_sessions
                                    (calendar_id, session_date,
                                     session_open_utc, session_close_utc)
                                VALUES (%(calendar_id)s, %(session_date)s,
                                        %(session_open_utc)s, %(session_close_utc)s)
                                ON CONFLICT (calendar_id, session_date) DO UPDATE
                                    SET session_open_utc  = EXCLUDED.session_open_utc,
                                        session_close_utc = EXCLUDED.session_close_utc
                                """,
                                rows,
                            )
                            inserted = cur.rowcount
                        conn.commit()
                    total_inserted += inserted

            # Check horizon health after extension
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT MAX(session_date) AS max_date "
                        "FROM trading_sessions WHERE calendar_id = %s",
                        (cal_id,),
                    )
                    new_row = cur.fetchone()
                    new_max = new_row["max_date"] if new_row else None

            if new_max is None or new_max < warn_cutoff:
                days_remaining = (new_max - today).days if new_max else 0
                horizon_warnings.append(
                    f"{cal_id}: horizon ends {new_max} ({days_remaining} days remaining)"
                )

    print_result(
        f"{total_inserted} sessions inserted, 0 updated.",
        json_mode=False,
    )

    if horizon_warnings and strict:
        for warning in horizon_warnings:
            print_error(f"Horizon warning: {warning}", json_mode=False)
        raise typer.Exit(_EXIT_HORIZON_WARN)


# ---------------------------------------------------------------------------
# mt data rechunk — slice 166 one-shot hypertable re-chunk maintenance
# ---------------------------------------------------------------------------

_EXIT_RECHUNK_FAILED: int = 2


@data_app.command("rechunk")
def data_rechunk(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report the window plan and counts; mutate nothing.",
    ),
) -> None:
    """Rewrite minute_ohlcv's 4-hour chunks into 7-day chunks (slice 166).

    One window per transaction; resumable and idempotent (window state is
    re-derived from the Timescale catalog each run). Pre-flight refuses to
    run unless migration 043 is applied and the minute-family background
    jobs are paused.

    OPERATOR: stop the data daemon and any `mt data pull` / gap-seeding
    processes before a real run. Each window transaction takes an EXCLUSIVE
    lock on minute_ohlcv (writers block for that window's duration, readers
    are unaffected), so concurrent writers cannot lose rows — but they would
    stall repeatedly across ~1,175 windows.

    Exit codes:
      0   success (or dry run)
      1   MT_TIMESCALE_DB_URL not configured, or pre-flight refused
      2   a window cycle failed (failing window identified on stderr)
    """
    import psycopg as _psycopg

    from manta_trading.market.maintenance.rechunk import (
        PreflightError,
        RechunkError,
        run_rechunk,
    )

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=False)
        raise typer.Exit(_EXIT_PREFLIGHT_FAILED)

    try:
        result = run_rechunk(settings.timescale_db_url, dry_run=dry_run)
    except PreflightError as exc:
        print_error(f"Pre-flight refused: {exc}", json_mode=False)
        raise typer.Exit(_EXIT_PREFLIGHT_FAILED) from exc
    except RechunkError as exc:
        print_error(f"Rechunk failed: {exc}", json_mode=False)
        raise typer.Exit(_EXIT_RECHUNK_FAILED) from exc
    except _psycopg.OperationalError as exc:
        print_error(f"Database unreachable: {exc}", json_mode=False)
        raise typer.Exit(_EXIT_PREFLIGHT_FAILED) from exc

    mode = "DRY RUN — no changes made" if result.dry_run else "complete"
    print_result(
        f"Rechunk {mode}: {result.total_windows} windows "
        f"({result.rewritten} rewritten, {result.compressed_only} compressed-only, "
        f"{result.skipped_uncompressed} skipped uncompressed, "
        f"{result.already_done} already done).",
        json_mode=False,
    )


# ---------------------------------------------------------------------------
# mt data daemon — slice 146 long-running daemon (T27)
# ---------------------------------------------------------------------------


@daemon_app.command("run")
def daemon_run(
    ctx: typer.Context,
    symbols_opt: str | None = typer.Option(
        None,
        "--symbols",
        help="Comma-separated symbols to process (implies --stop-when-done by default).",
    ),
    list_name: str | None = typer.Option(
        None,
        "--list",
        help="Named list from symbol-lists.yaml (implies --stop-when-done by default).",
    ),
    minute: bool = typer.Option(False, "--minute", help="Run minute cycles. (Default: both run if neither flag given.)"),
    daily: bool = typer.Option(False, "--daily", help="Run daily cycles. (Default: both run if neither flag given.)"),
    max_credits: int | None = typer.Option(
        None, "--max-credits", help="Exit after spending this many credits."
    ),
    stop_when_done: bool | None = typer.Option(
        None,
        "--stop-when-done/--forever",
        help=(
            "Exit when the scope is drained. Default: True when --symbols/--list given, "
            "False otherwise."
        ),
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to symbol-lists.yaml (default: config/symbol-lists.yaml).",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print one line per symbol as it completes."
    ),
) -> None:
    """Run the long-running acquisition daemon.

    Loops indefinitely (or until scope is drained) running daily and minute
    cycles, plus a once-per-UTC-day bulk CA update.

    Use Ctrl-C or SIGTERM to exit cleanly.
    """
    import sys

    from manta_trading.data.acquisition.daemon.runner import (
        SCOPE_ALL_ACTIVE,
        Runner,
        RunnerConfig,
        make_ca_update_fn,
    )
    from manta_trading.data.acquisition.quota import QuotaBucket

    settings = ctx.obj["settings"]

    # Resolve scope.
    symbols_list: list[str] | None = None
    if symbols_opt is not None:
        symbols_list = [s.strip() for s in symbols_opt.split(",") if s.strip()]
    elif list_name is not None:
        from manta_trading.data.lists import ListNotFoundError, resolve_list_merged

        cfg = _resolve_lists_config_path(config)
        try:
            symbols_list = resolve_list_merged(list_name, cfg)
        except ListNotFoundError as exc:
            print_error(str(exc), json_mode=False)
            raise typer.Exit(1)

    # Termination default: scoped → drain, global → run forever.
    if stop_when_done is None:
        terminate_when_drained = symbols_list is not None
    else:
        terminate_when_drained = stop_when_done

    # If neither --daily nor --minute given, run both. If one is given, run only that.
    if not daily and not minute:
        granularities: set[str] = {"daily", "minute"}
    else:
        granularities = set()
        if daily:
            granularities.add("daily")
        if minute:
            granularities.add("minute")

    config_obj = RunnerConfig(
        scope=tuple(symbols_list) if symbols_list is not None else SCOPE_ALL_ACTIVE,
        granularities=frozenset(granularities),
        max_credits=max_credits,
        terminate_when_drained=terminate_when_drained,
    )

    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL is not set.", json_mode=False)
        raise typer.Exit(1)

    import psycopg

    def _conn_factory() -> psycopg.Connection:
        return psycopg.connect(settings.timescale_db_url)

    def on_symbol_cb(sym: str, outcome: str, recent_end: "datetime | None", oldest_end: "datetime | None", n: int) -> None:
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d-%H:%M:%S")
        if recent_end is not None and oldest_end is not None:
            window = f"  {oldest_end.strftime('%Y%m%d')} - {recent_end.strftime('%Y%m%d')}  ({n} chunk{'s' if n != 1 else ''})"
        elif n == 0:
            window = "  (current)"
        else:
            window = ""
        print(f"{sym:<12} {outcome:<17} {ts}{window}")

    on_symbol_cb = on_symbol_cb if verbose else None

    from manta_trading.data.acquisition.daemon.daily import run_daily_cycle as _rdc
    from manta_trading.data.acquisition.daemon.minute import run_minute_cycle as _rmc

    def _daily_cycle(**kwargs):  # type: ignore[no-untyped-def]
        return _rdc(**kwargs, on_symbol=on_symbol_cb)

    def _minute_cycle(**kwargs):  # type: ignore[no-untyped-def]
        return _rmc(**kwargs, on_symbol=on_symbol_cb)

    bucket = QuotaBucket()
    runner = Runner(
        config_obj,
        bucket,
        _conn_factory,
        run_ca_update=make_ca_update_fn(settings),
        run_daily_cycle=_daily_cycle if verbose else None,
        run_minute_cycle=_minute_cycle if verbose else None,
    )

    from manta_trading.data.maintenance.auto_extend import maybe_extend_trading_sessions
    runner.register_idle_hook(
        lambda: maybe_extend_trading_sessions(_conn_factory)
    )

    sys.exit(runner.start())




# ---------------------------------------------------------------------------
# mt data lists — slice 146 named-symbol-list CLI
# ---------------------------------------------------------------------------


def _resolve_lists_config_path(override: Path | None) -> Path:
    """Return the symbol-lists config path, defaulting to project relative."""
    return override if override is not None else _DEFAULT_LISTS_CONFIG


@lists_app.command("ls")
def lists_ls(
    ctx: typer.Context,
    config: Path = typer.Option(
        None,
        "--config",
        help="Path to symbol-lists.yaml (default: config/symbol-lists.yaml).",
    ),
) -> None:
    """List defined symbol lists with member counts."""
    from manta_trading.data.lists import ListsConfigError, load_lists_merged

    json_output = ctx.obj.get("json_output", False) if ctx.obj else False
    cfg = _resolve_lists_config_path(config)
    try:
        lists = load_lists_merged(cfg)
    except ListsConfigError as exc:
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(1)

    if json_output:
        print_result(
            {name: {"count": len(symbols)} for name, symbols in lists.items()},
            json_mode=True,
        )
        return

    table = make_table(
        f"Symbol lists ({cfg})",
        [("Name", "cyan"), ("Count", "green")],
    )
    for name, symbols in sorted(lists.items()):
        table.add_row(name, str(len(symbols)))
    from rich import print as rprint

    rprint(table)


@lists_app.command("show")
def lists_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="List name as defined in symbol-lists.yaml."),
    config: Path = typer.Option(
        None,
        "--config",
        help="Path to symbol-lists.yaml (default: config/symbol-lists.yaml).",
    ),
) -> None:
    """Print resolved symbols for ``name``, one per line."""
    from manta_trading.data.lists import (
        ListNotFoundError,
        ListsConfigError,
        resolve_list_merged,
    )

    json_output = ctx.obj.get("json_output", False) if ctx.obj else False
    cfg = _resolve_lists_config_path(config)
    try:
        symbols = resolve_list_merged(name, cfg)
    except ListNotFoundError as exc:
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(1)
    except ListsConfigError as exc:
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(1)

    if json_output:
        print_result({"name": name, "symbols": symbols}, json_mode=True)
        return

    for sym in symbols:
        typer.echo(sym)


@lists_app.command("refresh-sp500")
def lists_refresh_sp500(
    ctx: typer.Context,
    snapshot: Path = typer.Option(
        Path("config/lists/sp500-snapshot.txt"),
        "--snapshot",
        help="Path to write the refreshed S&P 500 snapshot.",
    ),
) -> None:
    """Refresh the S&P 500 snapshot from EODHD ``/fundamentals/GSPC.INDX``."""
    import httpx

    from manta_trading.data.lists import ListsConfigError, refresh_sp500

    json_output = ctx.obj.get("json_output", False) if ctx.obj else False
    api_key = _validate_credentials(ctx, json_output)
    if api_key is None:
        raise typer.Exit(1)

    url = "https://eodhd.com/api/fundamentals/GSPC.INDX"
    params = {"api_token": api_key, "fmt": "json"}

    def _fetch() -> dict:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 403:
                print_error(
                    "EODHD returned 403 Forbidden for /fundamentals/GSPC.INDX. "
                    "This endpoint requires a higher-tier EODHD subscription.",
                    json_mode=json_output,
                )
                raise typer.Exit(1)
            resp.raise_for_status()
            return resp.json()

    try:
        n = refresh_sp500(snapshot, _fetch)
    except typer.Exit:
        raise
    except ListsConfigError as exc:
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(1)
    except httpx.HTTPStatusError as exc:
        print_error(
            f"EODHD request failed: {exc.response.status_code} {exc.response.reason_phrase}",
            json_mode=json_output,
        )
        raise typer.Exit(1)

    if json_output:
        print_result({"path": str(snapshot), "count": n}, json_mode=True)
    else:
        typer.echo(f"refreshed {snapshot}: {n} symbols")


# ---------------------------------------------------------------------------
# mt data ca — slice 146 corporate-actions CLI (T21, T22)
# ---------------------------------------------------------------------------

_CA_BULK_ROW_LIMIT = 1000
"""Maximum rows returned by ``mt data ca list`` before the pagination footer."""

_CA_DATE_FORMAT = "%Y-%m-%d"


def _parse_since_arg(since: str | None) -> tuple[int | None, object]:
    """Parse ``--since`` into ``(days: int | None, since_date: date | None)``.

    Returns a 2-tuple.  Exactly one of the two return values will be non-None
    when ``since`` is not None; both are None when ``since`` is None.
    """
    import re
    from datetime import date as _date

    if since is None:
        return None, None
    if re.fullmatch(r"\d+", since):
        return int(since), None
    try:
        return None, _date.fromisoformat(since)
    except ValueError as exc:
        raise typer.BadParameter(
            f"--since must be an integer (days) or YYYY-MM-DD date: {since!r}"
        ) from exc


def _yesterday_utc() -> object:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date() - __import__("datetime").timedelta(days=1)


@ca_app.command("update")
def ca_update(
    ctx: typer.Context,
    since: str | None = typer.Option(
        None,
        "--since",
        help=(
            "Trailing window: integer = last N days (per-day bulk fetch); "
            "YYYY-MM-DD = from that date through yesterday (per-day bulk). "
            "Omit for yesterday-only bulk fetch (200 credits)."
        ),
    ),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Per-symbol full-history backfill via /splits + /div (2 credits).",
    ),
    list_name: str | None = typer.Option(
        None,
        "--list",
        help="Per-symbol backfill across each member of a named list.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to symbol-lists.yaml (default: config/symbol-lists.yaml).",
    ),
) -> None:
    """Fetch and upsert splits + dividends.

    No flags: bulk-fetch yesterday's splits + dividends (200 credits).
    --since N / --since YYYY-MM-DD: bulk-fetch a trailing window, per day.
    --symbol X: per-symbol full-history backfill (2 credits).
    --list NAME: per-symbol backfill for each member of a named list.
    --symbol and --list are mutually exclusive.
    """
    import asyncio
    from datetime import date as _date, timedelta

    from manta_trading.data.acquisition.quota import CallType, QuotaBucket

    if symbol is not None and list_name is not None:
        print_error("--symbol and --list are mutually exclusive.", json_mode=False)
        raise typer.Exit(1)

    settings = ctx.obj["settings"]

    api_key = _validate_credentials(ctx, False)
    if api_key is None:
        raise typer.Exit(1)

    bucket = QuotaBucket()

    if symbol is not None or list_name is not None:
        # Per-symbol path (--symbol or --list).
        from manta_trading.data.adjustment import ingest_corporate_actions

        symbols: list[str] = []
        if symbol is not None:
            symbols = [symbol]
        else:
            from manta_trading.data.lists import ListNotFoundError, resolve_list_merged

            cfg = _resolve_lists_config_path(config)
            try:
                symbols = resolve_list_merged(list_name, cfg)
            except ListNotFoundError as exc:
                print_error(str(exc), json_mode=False)
                raise typer.Exit(1)

        total_added = total_updated = 0
        for sym in symbols:
            try:
                result = asyncio.run(ingest_corporate_actions(sym, settings=settings))
                total_added += result.splits_added + result.dividends_added
                total_updated += result.splits_updated + result.dividends_updated
                typer.echo(
                    f"{sym}: splits +{result.splits_added}/~{result.splits_updated} "
                    f"div +{result.dividends_added}/~{result.dividends_updated}"
                )
            except Exception:
                logger.exception("ca update --symbol %s failed", sym)
                typer.echo(f"{sym}: ERROR (see logs)", err=True)
        typer.echo(f"done: {total_added} added, {total_updated} updated")
        return

    # Bulk path (no --symbol / --list).
    import httpx

    from manta_trading.data.adjustment.ingest import upsert_dividends, upsert_splits
    from manta_trading.data.adjustment.providers.bulk_ca import (
        fetch_bulk_dividends,
        fetch_bulk_splits,
    )

    days_arg, since_date_arg = _parse_since_arg(since)

    today = _date.today()
    yesterday = today - timedelta(days=1)

    if days_arg is not None:
        dates = [yesterday - timedelta(days=i) for i in range(days_arg - 1, -1, -1)]
    elif since_date_arg is not None:
        since_d: _date = since_date_arg  # type: ignore[assignment]
        n_days = (yesterday - since_d).days + 1
        if n_days <= 0:
            print_error("--since date must be before yesterday.", json_mode=False)
            raise typer.Exit(1)
        dates = [since_d + timedelta(days=i) for i in range(n_days)]
    else:
        dates = [yesterday]

    total_s_add = total_s_upd = total_d_add = total_d_upd = 0
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    with httpx.Client(timeout=30.0) as client:
        token = QUOTA_BUCKET_VAR.set(bucket)
        try:
            for d in dates:
                splits = fetch_bulk_splits(client, d, api_key=api_key)
                divs = fetch_bulk_dividends(client, d, api_key=api_key)
                if not settings.timescale_db_url:
                    print_error("MT_TIMESCALE_DB_URL is not set.", json_mode=False)
                    raise typer.Exit(1)
                s_add, s_upd = upsert_splits(str(settings.timescale_db_url), splits)
                d_add, d_upd = upsert_dividends(str(settings.timescale_db_url), divs)
                total_s_add += s_add
                total_s_upd += s_upd
                total_d_add += d_add
                total_d_upd += d_upd
                typer.echo(
                    f"{d}: splits +{s_add}/~{s_upd}  dividends +{d_add}/~{d_upd} "
                    f"[credits used: {bucket.spent_today()}]"
                )
        finally:
            QUOTA_BUCKET_VAR.reset(token)

    typer.echo(
        f"done: splits {total_s_add} added / {total_s_upd} updated; "
        f"dividends {total_d_add} added / {total_d_upd} updated"
    )


@ca_app.command("show")
def ca_show(
    ctx: typer.Context,
    symbol: str = typer.Option(..., "--symbol", help="Symbol to inspect (e.g. AAPL)."),
    from_date: str | None = typer.Option(
        None, "--from", help="Inclusive lower bound (YYYY-MM-DD)."
    ),
    to_date: str | None = typer.Option(
        None, "--to", help="Inclusive upper bound (YYYY-MM-DD)."
    ),
) -> None:
    """Show splits and dividends for a symbol in a date window."""
    from datetime import date as _date

    import psycopg
    from rich.console import Console
    from rich.table import Table

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL is not set.", json_mode=False)
        raise typer.Exit(1)

    def _parse_date_opt(raw: str | None, label: str) -> _date | None:
        if raw is None:
            return None
        try:
            return _date.fromisoformat(raw)
        except ValueError as exc:
            print_error(f"{label} must be YYYY-MM-DD: {raw!r}", json_mode=False)
            raise typer.Exit(1) from exc

    from_d = _parse_date_opt(from_date, "--from")
    to_d = _parse_date_opt(to_date, "--to")

    with psycopg.connect(str(settings.timescale_db_url)) as conn:
        splits = _query_splits(conn, symbol, from_d, to_d)
        dividends = _query_dividends(conn, symbol, from_d, to_d)

    console = Console()
    if splits:
        t = Table(title=f"Splits — {symbol}", show_lines=False)
        t.add_column("ex_date")
        t.add_column("ratio_to", justify="right")
        t.add_column("ratio_from", justify="right")
        t.add_column("source")
        for row in splits:
            t.add_row(str(row["ex_date"]), str(row["ratio_to"]), str(row["ratio_from"]), row["source"])
        console.print(t)
    else:
        console.print(f"[dim]No splits for {symbol}[/dim]")

    if dividends:
        t2 = Table(title=f"Dividends — {symbol}", show_lines=False)
        t2.add_column("ex_date")
        t2.add_column("amount", justify="right")
        t2.add_column("currency")
        t2.add_column("source")
        for row in dividends:
            t2.add_row(str(row["ex_date"]), str(row["amount"]), row["currency"], row["source"])
        console.print(t2)
    else:
        console.print(f"[dim]No dividends for {symbol}[/dim]")


@ca_app.command("list")
def ca_list(
    ctx: typer.Context,
    from_date: str | None = typer.Option(
        None, "--from", help="Inclusive lower bound (YYYY-MM-DD)."
    ),
    to_date: str | None = typer.Option(
        None, "--to", help="Inclusive upper bound (YYYY-MM-DD)."
    ),
) -> None:
    """List all corporate actions in a date window (capped at 1000 rows).

    Use --symbol with ``mt data ca show`` to scope to a single symbol.
    """
    from datetime import date as _date

    import psycopg
    from rich.console import Console
    from rich.table import Table

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL is not set.", json_mode=False)
        raise typer.Exit(1)

    def _parse_date_opt(raw: str | None, label: str) -> _date | None:
        if raw is None:
            return None
        try:
            return _date.fromisoformat(raw)
        except ValueError as exc:
            print_error(f"{label} must be YYYY-MM-DD: {raw!r}", json_mode=False)
            raise typer.Exit(1) from exc

    from_d = _parse_date_opt(from_date, "--from")
    to_d = _parse_date_opt(to_date, "--to")

    limit = _CA_BULK_ROW_LIMIT + 1  # fetch one extra to detect overflow

    with psycopg.connect(str(settings.timescale_db_url)) as conn:
        splits = _query_splits(conn, None, from_d, to_d, limit=limit)
        dividends = _query_dividends(conn, None, from_d, to_d, limit=limit)

    console = Console()

    overflow = False
    if len(splits) > _CA_BULK_ROW_LIMIT:
        splits = splits[:_CA_BULK_ROW_LIMIT]
        overflow = True
    if len(dividends) > _CA_BULK_ROW_LIMIT:
        dividends = dividends[:_CA_BULK_ROW_LIMIT]
        overflow = True

    t = Table(title="Splits", show_lines=False)
    t.add_column("symbol")
    t.add_column("ex_date")
    t.add_column("ratio_to", justify="right")
    t.add_column("ratio_from", justify="right")
    for row in splits:
        t.add_row(row["symbol"], str(row["ex_date"]), str(row["ratio_to"]), str(row["ratio_from"]))
    console.print(t)

    t2 = Table(title="Dividends", show_lines=False)
    t2.add_column("symbol")
    t2.add_column("ex_date")
    t2.add_column("amount", justify="right")
    t2.add_column("currency")
    for row in dividends:
        t2.add_row(row["symbol"], str(row["ex_date"]), str(row["amount"]), row["currency"])
    console.print(t2)

    if overflow:
        console.print(
            f"[yellow]Results capped at {_CA_BULK_ROW_LIMIT} rows per table. "
            "Use --symbol with `mt data ca show` to scope.[/yellow]"
        )


# ---------------------------------------------------------------------------
# CA query helpers (used by ca_show and ca_list)
# ---------------------------------------------------------------------------


def _query_splits(
    conn: object,
    symbol: str | None,
    from_d: object,
    to_d: object,
    limit: int | None = None,
) -> list[dict]:
    """Query splits table; symbol=None means all symbols."""
    params: list[object] = []
    where: list[str] = []
    if symbol is not None:
        where.append("symbol = %s")
        params.append(symbol)
    if from_d is not None:
        where.append("ex_date >= %s")
        params.append(from_d)
    if to_d is not None:
        where.append("ex_date <= %s")
        params.append(to_d)

    sql = "SELECT symbol, ex_date, ratio_to, ratio_from, source FROM splits"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ex_date, symbol"
    if limit is not None:
        sql += f" LIMIT {limit}"

    with _ca_cursor(conn) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _query_dividends(
    conn: object,
    symbol: str | None,
    from_d: object,
    to_d: object,
    limit: int | None = None,
) -> list[dict]:
    """Query dividends table; symbol=None means all symbols."""
    params: list[object] = []
    where: list[str] = []
    if symbol is not None:
        where.append("symbol = %s")
        params.append(symbol)
    if from_d is not None:
        where.append("ex_date >= %s")
        params.append(from_d)
    if to_d is not None:
        where.append("ex_date <= %s")
        params.append(to_d)

    sql = "SELECT symbol, ex_date, amount, currency, source FROM dividends"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ex_date, symbol"
    if limit is not None:
        sql += f" LIMIT {limit}"

    with _ca_cursor(conn) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _ca_cursor(conn: object) -> object:
    """Open a psycopg cursor that returns rows as dicts."""
    from psycopg.rows import dict_row

    return conn.cursor(row_factory=dict_row)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# mt data get — read OHLCV bars (slice 154)
# ---------------------------------------------------------------------------


@data_app.command("get")
def data_get(
    ctx: typer.Context,
    symbol: str = typer.Argument(..., help="Ticker symbol (e.g. AAPL)."),
    granularity: str = typer.Argument(
        ..., help="Granularity token: 1m 5m 15m 1h 4h 1d 1w 1mo 1q"
    ),
    start: str | None = typer.Option(None, "--start", help="Start date (YYYY-MM-DD)."),
    end: str | None = typer.Option(None, "--end", help="End date (YYYY-MM-DD)."),
    raw: bool = typer.Option(False, "--raw", help="Return unadjusted bars."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    csv_output: bool = typer.Option(False, "--csv", help="Emit CSV."),
) -> None:
    """Read OHLCV bars for a symbol.

    Routes to TimescaleDailyDataDB for daily/coarser granularities (1d, 1w,
    1mo, 1q) and to TimescaleMinuteDataDB for sub-daily tokens (1m, 5m, 15m,
    1h, 4h). Adjusted by default; use --raw for unadjusted bars.

    Exit codes:
      0  success
      1  configuration error, invalid granularity, or missing data
    """
    import json as _json
    from datetime import date as _date, datetime as _datetime, timezone as _tz

    from manta_trading.constants import Granularity

    symbol = symbol.upper()

    # Validate granularity.
    try:
        gran = Granularity(granularity)
    except ValueError:
        valid = ", ".join(g.value for g in Granularity)
        print_error(
            f"Unknown granularity '{granularity}'. Valid tokens: {valid}",
            json_mode=json_output,
        )
        raise typer.Exit(1)

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)

    # Parse date args.
    default_start = _date(1970, 1, 1)
    default_end = _date.today()

    parsed_start: _date = default_start
    parsed_end: _date = default_end

    if start is not None:
        try:
            parsed_start = _date.fromisoformat(start)
        except ValueError:
            print_error(
                f"--start '{start}' is not a valid date (YYYY-MM-DD).",
                json_mode=json_output,
            )
            raise typer.Exit(1)

    if end is not None:
        try:
            parsed_end = _date.fromisoformat(end)
        except ValueError:
            print_error(
                f"--end '{end}' is not a valid date (YYYY-MM-DD).",
                json_mode=json_output,
            )
            raise typer.Exit(1)

    _DAILY_GRAINS = {
        Granularity.D1, Granularity.W1, Granularity.MO1, Granularity.Q1
    }

    try:
        if gran in _DAILY_GRAINS:
            from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB

            db = TimescaleDailyDataDB(conninfo=settings.timescale_db_url)
            try:
                df = db.get_daily_data(
                    symbol, parsed_start, parsed_end, gran, adjusted=not raw
                )
            finally:
                db.close()
        else:
            # Minute-grain: convert dates to datetimes.
            start_dt = _datetime(
                parsed_start.year, parsed_start.month, parsed_start.day,
                tzinfo=_tz.utc,
            )
            end_dt = _datetime(
                parsed_end.year, parsed_end.month, parsed_end.day,
                23, 59, 59, tzinfo=_tz.utc,
            )

            from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

            db_m = TimescaleMinuteDataDB(conninfo=settings.timescale_db_url)
            try:
                agg = gran.value if gran != Granularity.M1 else None
                df = db_m.get_minute_data(
                    symbol, start_dt, end_dt,
                    aggregation=agg,
                    adjusted=not raw,
                )
            finally:
                db_m.close()
    except KeyError as exc:
        # missing prev_close in adjusted() surfaces as KeyError
        print_error(
            f"Adjustment data missing for {symbol} near date {exc} — "
            "run `mt data ca update --symbol {symbol}` to populate corporate actions.",
            json_mode=json_output,
        )
        raise typer.Exit(1)

    if df.empty:
        msg = f"No data for {symbol} {granularity} in range {parsed_start} – {parsed_end}"
        if json_output:
            print_result({"symbol": symbol, "granularity": granularity, "rows": []}, json_mode=True)
        else:
            print_result(msg, json_mode=False)
        return

    # Output modes.
    if csv_output:
        import sys
        df.index.name = "trade_date"
        sys.stdout.write(df.to_csv())
        return

    if json_output:
        df.index.name = "trade_date"
        records = df.reset_index().to_dict(orient="records")
        # Convert date/datetime to ISO strings for serialisation.
        for rec in records:
            for k, v in rec.items():
                if hasattr(v, "isoformat"):
                    rec[k] = v.isoformat()
        print_result(
            {"symbol": symbol, "granularity": granularity, "rows": records},
            json_mode=True,
        )
        return

    # Rich table.
    from rich.console import Console
    from rich.table import Table as RichTable

    table = RichTable(title=f"{symbol} {granularity} ({'raw' if raw else 'adjusted'})")
    table.add_column("Date")
    table.add_column("Open", justify="right")
    table.add_column("High", justify="right")
    table.add_column("Low", justify="right")
    table.add_column("Close", justify="right")
    table.add_column("Volume", justify="right")

    for trade_date, row in df.iterrows():
        date_str = (
            trade_date.strftime("%Y-%m-%d")
            if hasattr(trade_date, "strftime")
            else str(trade_date)
        )
        table.add_row(
            date_str,
            f"{row['open']:.4f}",
            f"{row['high']:.4f}",
            f"{row['low']:.4f}",
            f"{row['close']:.4f}",
            f"{int(row['volume']):,}",
        )
    Console().print(table)
    print_result(f"\n{len(df)} row(s)", json_mode=False)


# ---------------------------------------------------------------------------
# mt data pull — fetch / verify / reset gaps (slice 154)
# ---------------------------------------------------------------------------

# Terminal gap statuses that --reset clears.
_TERMINAL_GAP_STATUSES: frozenset[str] = frozenset(
    {"PROVIDER_HOLE", "RETRY_EXHAUSTED"}
)

# Granularities accepted by pull (raw sources only).
_PULL_GRANULARITIES: frozenset[str] = frozenset({"1d", "1m"})


def _resolve_symbols_for_pull(
    *,
    symbol: str | None,
    symbols_opt: str | None,
    list_name: str | None,
    universe: bool,
    include_delisted: bool,
    settings,
    config_path: "Path",
    json_output: bool,
) -> list[str]:
    """Resolve the symbol list for pull/verify/reset operations.

    Exactly one of symbol/symbols_opt/list_name/universe must be provided.
    Raises typer.Exit(1) with a clear error if none or more than one are set.

    include_delisted: only consulted when universe=True. When False (default),
    restricts the universe query to active-only instruments
    (delisted_at_eodhd = FALSE AND delisted_date IS NULL). When True, returns
    all instruments. Passing include_delisted=True without universe=True is an
    error (exit 1).
    """
    import psycopg

    if include_delisted and not universe:
        print_error(
            "--include-delisted requires --universe.",
            json_mode=json_output,
        )
        raise typer.Exit(1)

    provided = sum([
        symbol is not None,
        symbols_opt is not None,
        list_name is not None,
        universe,
    ])
    if provided == 0:
        print_error(
            "Symbol selection required. Use one of: "
            "--symbol, --symbols, --list, --universe",
            json_mode=json_output,
        )
        raise typer.Exit(1)
    if provided > 1:
        print_error(
            "--symbol, --symbols, --list, and --universe are mutually exclusive.",
            json_mode=json_output,
        )
        raise typer.Exit(1)

    if symbol is not None:
        return [symbol.upper()]

    if symbols_opt is not None:
        return [s.strip().upper() for s in symbols_opt.split(",") if s.strip()]

    if list_name is not None:
        from manta_trading.data.lists import ListNotFoundError, resolve_list_merged

        try:
            return resolve_list_merged(list_name, config_path)
        except ListNotFoundError as exc:
            print_error(str(exc), json_mode=json_output)
            raise typer.Exit(1)

    # universe: query instruments table directly to avoid daemon-path semantics
    # in iter_active_instruments (its "one final pass" for newly-delisted symbols
    # is correct for the daemon but wrong for an explicit pull operation).
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)
    if include_delisted:
        sql = "SELECT symbol FROM instruments ORDER BY symbol ASC"
    else:
        sql = (
            "SELECT symbol FROM instruments"
            " WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL"
            " ORDER BY symbol ASC"
        )
    with psycopg.connect(settings.timescale_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [row[0] for row in cur.fetchall()]


@data_app.command("pull")
def data_pull(
    ctx: typer.Context,
    granularity: str = typer.Argument(
        ..., help="Granularity to pull: 1d or 1m only."
    ),
    symbol: str | None = typer.Option(
        None, "--symbol", help="Single symbol."
    ),
    symbols_opt: str | None = typer.Option(
        None, "--symbols", help="Comma-separated symbols."
    ),
    list_name: str | None = typer.Option(
        None, "--list", help="Named list from symbol-lists.yaml."
    ),
    universe: bool = typer.Option(
        False, "--universe", help="All active instruments."
    ),
    include_delisted: bool = typer.Option(
        False,
        "--include-delisted",
        help="Include delisted instruments. Only valid with --universe.",
    ),
    start: str | None = typer.Option(None, "--start", help="Start date (YYYY-MM-DD)."),
    end: str | None = typer.Option(None, "--end", help="End date (YYYY-MM-DD)."),
    verify: bool = typer.Option(
        False, "--verify", help="Report gaps; fetch nothing."
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Reset terminal gap markers to UNKNOWN before fetching.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview actions without making changes."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt for --reset."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print one line per symbol as it completes."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    config: "Path | None" = typer.Option(
        None,
        "--config",
        help="Path to symbol-lists.yaml (default: config/symbol-lists.yaml).",
    ),
) -> None:
    """Fetch, verify, or reset data gaps for a symbol set.

    Granularity must be 1d (daily) or 1m (minute). Cagg tokens error clearly.
    Exactly one symbol selector is required: --symbol, --symbols, --list, or --universe.

    Default mode: fetch gaps in window, skip terminal gaps (PROVIDER_HOLE, RETRY_EXHAUSTED).
    --verify: report gaps without fetching.
    --reset: reset terminal markers before fetching (confirmation required unless --yes or --json).
    --dry-run: preview what would change without making changes.
    --verify and --dry-run are mutually exclusive.
    --include-delisted: expand --universe to include delisted instruments; requires --universe.

    Exit codes:
      0  success or dry-run/verify
      1  configuration error, invalid granularity, or no symbol selector
      2  operator declined confirmation
    """
    import json as _json
    from datetime import date as _date

    import psycopg as _psycopg

    _EXIT_DECLINED = 2

    # Validate granularity.
    if granularity not in _PULL_GRANULARITIES:
        from manta_trading.constants import Granularity

        try:
            Granularity(granularity)
            # It's a valid Granularity but not 1d/1m — cagg token.
            print_error(
                f"'{granularity}' is a derived/cagg granularity; "
                "pull only accepts raw source granularities: "
                + ", ".join(sorted(_PULL_GRANULARITIES)),
                json_mode=json_output,
            )
        except ValueError:
            print_error(
                f"Unknown granularity '{granularity}'. "
                "pull accepts: " + ", ".join(sorted(_PULL_GRANULARITIES)),
                json_mode=json_output,
            )
        raise typer.Exit(1)

    # Mutual exclusivity check.
    if verify and dry_run:
        print_error(
            "--verify and --dry-run are mutually exclusive.",
            json_mode=json_output,
        )
        raise typer.Exit(1)

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)

    cfg_path = _resolve_lists_config_path(config)
    symbols = _resolve_symbols_for_pull(
        symbol=symbol,
        symbols_opt=symbols_opt,
        list_name=list_name,
        universe=universe,
        include_delisted=include_delisted,
        settings=settings,
        config_path=cfg_path,
        json_output=json_output,
    )

    # Parse date window.
    parsed_start: _date | None = None
    parsed_end: _date | None = None
    if start is not None:
        try:
            parsed_start = _date.fromisoformat(start)
        except ValueError:
            print_error(
                f"--start '{start}' is not a valid date (YYYY-MM-DD).",
                json_mode=json_output,
            )
            raise typer.Exit(1)
    if end is not None:
        try:
            parsed_end = _date.fromisoformat(end)
        except ValueError:
            print_error(
                f"--end '{end}' is not a valid date (YYYY-MM-DD).",
                json_mode=json_output,
            )
            raise typer.Exit(1)

    # --verify: report gaps without fetching.
    if verify:
        _pull_verify(
            symbols=symbols,
            granularity=granularity,
            start=parsed_start,
            end=parsed_end,
            settings=settings,
            json_output=json_output,
        )
        return

    # --reset: preview terminal gaps and prompt for confirmation.
    if reset:
        terminal_gaps = _pull_fetch_terminal_gaps(
            symbols=symbols,
            granularity=granularity,
            start=parsed_start,
            end=parsed_end,
            settings=settings,
        )

        if dry_run:
            msg = (
                f"[dry-run] Would reset {len(terminal_gaps)} terminal gap(s) "
                f"for {len(symbols)} symbol(s), then fetch."
            )
            if json_output:
                print_result({"dry_run": True, "terminal_gaps": len(terminal_gaps), "symbols": len(symbols)}, json_mode=True)
            else:
                print_result(msg, json_mode=False)
            return

        if not yes and not json_output:
            print_result(
                f"About to reset {len(terminal_gaps)} terminal gap(s) to UNKNOWN "
                f"for {len(symbols)} symbol(s) and re-fetch.",
                json_mode=False,
            )
            confirm = typer.prompt("Type 'reset' to proceed", default="")
            if confirm.strip().lower() != "reset":
                print_error("Operator declined.", json_mode=json_output)
                raise typer.Exit(_EXIT_DECLINED)

        _pull_reset_and_fetch(
            symbols=symbols,
            granularity=granularity,
            start=parsed_start,
            end=parsed_end,
            settings=settings,
            json_output=json_output,
            verbose=verbose,
        )
        return

    if dry_run:
        # dry-run without --reset: show what gaps would be fetched.
        gaps = _pull_query_unknown_gaps(
            symbols=symbols,
            granularity=granularity,
            start=parsed_start,
            end=parsed_end,
            settings=settings,
        )
        cold = _pull_query_cold_symbols(
            symbols=symbols,
            granularity=granularity,
            settings=settings,
        )
        if json_output:
            print_result(
                {
                    "dry_run": True,
                    "gaps_to_fetch": len(gaps),
                    "cold_symbols": len(cold),
                    "symbols": len(symbols),
                },
                json_mode=True,
            )
        else:
            msg = f"[dry-run] Would fetch {len(gaps)} gap(s) for {len(symbols)} symbol(s)."
            if cold:
                floor = "1970-01-01" if granularity == "1d" else "2004-01-01"
                msg += (
                    f" {len(cold)} cold symbol(s) have no bars yet and will fetch"
                    f" from {floor}: {', '.join(cold[:10])}"
                    + (" ..." if len(cold) > 10 else ".")
                )
            print_result(msg, json_mode=False)
        return

    # Default: fetch gaps.
    _pull_fetch(
        symbols=symbols,
        granularity=granularity,
        start=parsed_start,
        end=parsed_end,
        settings=settings,
        json_output=json_output,
        verbose=verbose,
    )


def _pull_verify(
    *,
    symbols: list[str],
    granularity: str,
    start: _date_t | None,
    end: _date_t | None,
    settings,
    json_output: bool,
) -> None:
    """Report open gaps for the symbol set without fetching."""
    from datetime import date as _date

    gaps = _pull_query_unknown_gaps(
        symbols=symbols,
        granularity=granularity,
        start=start,
        end=end,
        settings=settings,
    )

    if json_output:
        print_result(
            {
                "mode": "verify",
                "granularity": granularity,
                "symbols": len(symbols),
                "open_gaps": len(gaps),
                "gaps": gaps,
            },
            json_mode=True,
        )
        return

    if not gaps:
        print_result(
            f"No open gaps for {len(symbols)} symbol(s) at {granularity}.",
            json_mode=False,
        )
        return

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title=f"Open gaps — {granularity} ({len(gaps)} gap(s))")
    for col in ("Symbol", "Gap Start", "Gap End", "Status", "Attempts"):
        table.add_column(col)
    for g in gaps:
        table.add_row(
            g["symbol"],
            str(g["gap_start"]),
            str(g["gap_end"]),
            g["fetch_status"],
            str(g["attempt_count"]),
        )
    console.print(table)


def _pull_query_unknown_gaps(
    *,
    symbols: list[str],
    granularity: str,
    start: _date_t | None,
    end: _date_t | None,
    settings,
) -> list[dict]:
    """Query non-terminal (UNKNOWN / IN_PROGRESS) gaps for the symbol set."""
    import psycopg

    from datetime import date as _date

    db_granularity = "daily" if granularity == "1d" else "minute"

    conditions = ["granularity = %s", "symbol = ANY(%s)", "fetch_status NOT IN ('PROVIDER_HOLE', 'RETRY_EXHAUSTED')"]
    params: list[object] = [db_granularity, symbols]

    if start is not None:
        conditions.append("gap_start >= %s")
        params.append(start)
    if end is not None:
        conditions.append("gap_end <= %s")
        params.append(end)

    sql = (
        "SELECT symbol, gap_start, gap_end, fetch_status, attempt_count "
        "FROM data_gaps WHERE " + " AND ".join(conditions) +
        " ORDER BY symbol, gap_start"
    )

    with psycopg.connect(settings.timescale_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "symbol": r[0],
            "gap_start": str(r[1]),
            "gap_end": str(r[2]),
            "fetch_status": r[3],
            "attempt_count": r[4],
        }
        for r in rows
    ]


def _pull_query_cold_symbols(
    *,
    symbols: list[str],
    granularity: str,
    settings,
) -> list[str]:
    """Return symbols that have no bars in the target table.

    These are cold symbols: the fetch will run from the history floor
    (DAILY_HISTORY_FLOOR for 1d, EODHD_INTRADAY_HORIZON for 1m) even
    though data_gaps shows no open gaps for them yet.
    """
    import psycopg

    table = "daily_ohlcv" if granularity == "1d" else "minute_ohlcv"
    # Table name is an internal constant — not user-supplied.
    sql = (
        f"SELECT s.sym FROM unnest(%s::text[]) AS s(sym) "  # noqa: S608
        f"WHERE NOT EXISTS ("
        f"  SELECT 1 FROM {table} WHERE symbol = s.sym LIMIT 1"
        f")"
    )
    with psycopg.connect(settings.timescale_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbols,))
            return [row[0] for row in cur.fetchall()]


def _pull_fetch_terminal_gaps(
    *,
    symbols: list[str],
    granularity: str,
    start: _date_t | None,
    end: _date_t | None,
    settings,
) -> list[dict]:
    """Query terminal gaps (PROVIDER_HOLE / RETRY_EXHAUSTED) for the symbol set."""
    import psycopg

    db_granularity = "daily" if granularity == "1d" else "minute"

    conditions = ["granularity = %s", "symbol = ANY(%s)", "fetch_status = ANY(%s)"]
    params: list[object] = [db_granularity, symbols, list(_TERMINAL_GAP_STATUSES)]

    if start is not None:
        conditions.append("gap_start >= %s")
        params.append(start)
    if end is not None:
        conditions.append("gap_end <= %s")
        params.append(end)

    sql = (
        "SELECT symbol, gap_start, gap_end, fetch_status, attempt_count "
        "FROM data_gaps WHERE " + " AND ".join(conditions) +
        " ORDER BY symbol, gap_start"
    )

    with psycopg.connect(settings.timescale_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "symbol": r[0],
            "gap_start": str(r[1]),
            "gap_end": str(r[2]),
            "fetch_status": r[3],
            "attempt_count": r[4],
        }
        for r in rows
    ]


def _pull_reset_and_fetch(
    *,
    symbols: list[str],
    granularity: str,
    start: _date_t | None,
    end: _date_t | None,
    settings,
    json_output: bool,
    verbose: bool = False,
) -> None:
    """Reset terminal gaps then dispatch per-symbol fetch."""
    import psycopg

    db_granularity = "daily" if granularity == "1d" else "minute"

    # Reset terminal gaps to UNKNOWN.
    conditions = ["granularity = %s", "symbol = ANY(%s)", "fetch_status = ANY(%s)"]
    params: list[object] = [db_granularity, symbols, list(_TERMINAL_GAP_STATUSES)]
    if start is not None:
        conditions.append("gap_start >= %s")
        params.append(start)
    if end is not None:
        conditions.append("gap_end <= %s")
        params.append(end)

    reset_sql = (
        "UPDATE data_gaps SET fetch_status = 'UNKNOWN', attempt_count = 0 "
        "WHERE " + " AND ".join(conditions)
    )
    with psycopg.connect(settings.timescale_db_url) as conn:
        with conn.transaction():
            conn.execute(reset_sql, params)

    _pull_fetch(
        symbols=symbols,
        granularity=granularity,
        start=start,
        end=end,
        settings=settings,
        json_output=json_output,
        verbose=verbose,
    )


def _pull_fetch(
    *,
    symbols: list[str],
    granularity: str,
    start: _date_t | None,
    end: _date_t | None,
    settings,
    json_output: bool,
    verbose: bool = False,
) -> None:
    """Run the fetch cycle for the given symbols and granularity.

    Sets QUOTA_BUCKET_VAR so eodhd_get can consume credits (same as the
    daemon runner does). A fresh QuotaBucket is created per invocation;
    CLI callers are not subject to the daemon's rolling-24h accounting.
    """
    from manta_trading.data.acquisition.quota import QuotaBucket
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    bucket = QuotaBucket()
    token = QUOTA_BUCKET_VAR.set(bucket)
    try:
        _pull_fetch_inner(
            symbols=symbols,
            granularity=granularity,
            start=start,
            end=end,
            settings=settings,
            json_output=json_output,
            verbose=verbose,
        )
    finally:
        QUOTA_BUCKET_VAR.reset(token)


def _is_current(conn: "psycopg.Connection", symbol: str, granularity: str) -> bool:
    """Return True if symbol has bars and its most recent bar is recent enough to skip.

    For daily: last bar within 5 calendar days (covers weekends + holidays).
    For minute: last bar within 2 calendar days.

    Used by _pull_fetch_inner to skip symbols that are already up to date.
    Only consulted when no explicit --start/--end window is given.
    """
    from datetime import datetime, timedelta, timezone

    table = "daily_ohlcv" if granularity == "1d" else "minute_ohlcv"
    max_age = timedelta(days=5) if granularity == "1d" else timedelta(days=2)
    with conn.cursor() as cur:
        # Table name is an internal constant — not user-supplied.
        cur.execute(f"SELECT MAX(time) FROM {table} WHERE symbol = %s", (symbol,))  # noqa: S608
        row = cur.fetchone()
    if row is None or row[0] is None:
        return False
    last_bar: datetime = row[0]
    return (datetime.now(tz=timezone.utc) - last_bar) <= max_age


def _pull_fetch_inner(
    *,
    symbols: list[str],
    granularity: str,
    start: _date_t | None,
    end: _date_t | None,
    settings,
    json_output: bool,
    verbose: bool = False,
) -> None:
    """Inner fetch loop — assumes QUOTA_BUCKET_VAR is already set."""
    import psycopg

    # When no explicit window is given, skip symbols already within the
    # staleness threshold — avoids re-downloading full history on every run.
    check_current = start is None and end is None

    if granularity == "1d":
        from manta_trading.data.acquisition.daemon.daily import (
            run_daily_refetch,
        )
        success = 0
        fetched = 0
        skipped = 0
        failed: list[str] = []
        with psycopg.connect(settings.timescale_db_url) as conn:
            for sym in symbols:
                if check_current and _is_current(conn, sym, granularity):
                    skipped += 1
                    if verbose:
                        print(f"{sym:<12} {'current':<17}")
                    success += 1
                    continue
                report = run_daily_refetch(sym, from_date=start, to_date=end)
                fetched += report.success_count + report.partial_count + report.empty_count
                if report.success_count > 0 or report.partial_count > 0:
                    outcome = "ok"
                    success += 1
                elif report.empty_count > 0:
                    # No gaps to fill — counts as success for reporting purposes.
                    outcome = "empty"
                    success += 1
                else:
                    outcome = "failed"
                    failed.append(sym)
                if verbose:
                    print(f"{sym:<12} {outcome:<17}")
    else:
        # 1m: use minute refetch path.
        from manta_trading.data.acquisition.daemon.minute import (
            run_minute_refetch,
        )
        success = 0
        fetched = 0
        skipped = 0
        failed = []
        with psycopg.connect(settings.timescale_db_url) as conn:
            for sym in symbols:
                if check_current and _is_current(conn, sym, granularity):
                    skipped += 1
                    if verbose:
                        print(f"{sym:<12} {'current':<17}")
                    success += 1
                    continue
                report = run_minute_refetch(sym, from_date=start, to_date=end)
                fetched += report.success_count + report.partial_count + report.empty_count
                if report.success_count > 0 or report.partial_count > 0:
                    outcome = "ok"
                    success += 1
                elif report.empty_count > 0:
                    # No actionable gaps in window — not a failure.
                    outcome = "empty"
                    success += 1
                else:
                    outcome = "failed"
                    failed.append(sym)
                if verbose:
                    print(f"{sym:<12} {outcome:<17}")

    if json_output:
        print_result(
            {
                "granularity": granularity,
                "success": success,
                "skipped": skipped,
                "failed": len(failed),
                "failed_symbols": failed,
            },
            json_mode=True,
        )
        return

    parts = [f"{success} fetched"]
    if skipped:
        parts.append(f"{skipped} already current")
    if failed:
        parts.append(f"{len(failed)} failed")
    print_result("Pull complete: " + ", ".join(parts) + ".", json_mode=False)
    if failed:
        print_result(
            "Failed: " + ", ".join(failed[:20])
            + (" ..." if len(failed) > 20 else ""),
            json_mode=False,
        )


# ---------------------------------------------------------------------------
# mt data caggs — continuous aggregate management (slice 154)
# ---------------------------------------------------------------------------

# All 7 cagg views managed by this project.
_ALL_CAGG_VIEWS: list[tuple[str, str]] = [
    ("5m",  "minute_5min_ohlcv"),
    ("15m", "minute_15min_ohlcv"),
    ("1h",  "minute_hourly_ohlcv"),
    ("4h",  "minute_4hour_ohlcv"),
    ("1w",  "daily_weekly_ohlcv"),
    ("1mo", "daily_monthly_ohlcv"),
    ("1q",  "daily_quarterly_ohlcv"),
]


@caggs_app.command("refresh")
def caggs_refresh(
    ctx: typer.Context,
    granularity_opt: str | None = typer.Option(
        None,
        "--granularity",
        help="Comma-separated granularity tokens to refresh (default: all 7).",
    ),
    start: str | None = typer.Option(None, "--start", help="Refresh window start (YYYY-MM-DD)."),
    end: str | None = typer.Option(None, "--end", help="Refresh window end (YYYY-MM-DD)."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print one line per cagg as it completes."),
) -> None:
    """Manually refresh continuous aggregates.

    Calls CALL refresh_continuous_aggregate(...) for each matching cagg over
    the given window (or the full history if no window is specified).
    """
    import psycopg

    from manta_trading.constants import Granularity

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)

    # Resolve cagg subset.
    if granularity_opt is not None:
        requested = [t.strip() for t in granularity_opt.split(",") if t.strip()]
        # Validate each token.
        for token in requested:
            try:
                Granularity(token)
            except ValueError:
                valid = ", ".join(g.value for g in Granularity)
                print_error(
                    f"Unknown granularity token '{token}'. Valid: {valid}",
                    json_mode=json_output,
                )
                raise typer.Exit(1)
        cagg_subset = [(g, v) for g, v in _ALL_CAGG_VIEWS if g in requested]
        if not cagg_subset:
            print_error(
                f"None of the requested tokens ({', '.join(requested)}) "
                "map to a continuous aggregate.",
                json_mode=json_output,
            )
            raise typer.Exit(1)
    else:
        cagg_subset = _ALL_CAGG_VIEWS

    # Parse start/end strings to dates so psycopg can bind them as
    # timestamptz. When None we explicitly cast NULL in SQL — psycopg
    # cannot infer the type of a parameterised NULL against a procedure
    # argument, which the server reports as IndeterminateDatatype.
    from datetime import date as _date

    start_d: _date | None = None
    end_d: _date | None = None
    if start is not None:
        try:
            start_d = _date.fromisoformat(start)
        except ValueError:
            print_error(
                f"--start must be ISO date (YYYY-MM-DD); got '{start}'",
                json_mode=json_output,
            )
            raise typer.Exit(1)
    if end is not None:
        try:
            end_d = _date.fromisoformat(end)
        except ValueError:
            print_error(
                f"--end must be ISO date (YYYY-MM-DD); got '{end}'",
                json_mode=json_output,
            )
            raise typer.Exit(1)

    results: list[dict] = []
    with psycopg.connect(settings.timescale_db_url, autocommit=True) as conn:
        for gran_token, view_name in cagg_subset:
            if verbose:
                print(f"{gran_token:<6} {view_name} ...", flush=True)
            try:
                conn.execute(
                    "CALL refresh_continuous_aggregate("
                    "%s, %s::timestamptz, %s::timestamptz)",
                    (view_name, start_d, end_d),
                )
                results.append({"granularity": gran_token, "view": view_name, "status": "ok"})
                if verbose:
                    print(f"{gran_token:<6} {view_name} ok")
                logger.info("caggs refresh: %s (%s) done", gran_token, view_name)
            except Exception:
                logger.exception("caggs refresh: %s (%s) failed", gran_token, view_name)
                results.append({"granularity": gran_token, "view": view_name, "status": "error"})
                if verbose:
                    print(f"{gran_token:<6} {view_name} error")

    if json_output:
        print_result(results, json_mode=True)
        return

    table = make_table(
        "Cagg Refresh Results",
        [("Granularity", ""), ("View", ""), ("Status", "")],
    )
    for r in results:
        table.add_row(r["granularity"], r["view"], r["status"])
    print_result(table, json_mode=False)


# Maps each cagg granularity token to its source hypertable. Used by
# `caggs status` to compute the staleness signal (source MAX(time) vs
# cagg MAX(time_bucket)).
_CAGG_SOURCE_TABLE: dict[str, str] = {
    "5m": "minute_ohlcv",
    "15m": "minute_ohlcv",
    "1h": "minute_ohlcv",
    "4h": "minute_ohlcv",
    "1w": "daily_ohlcv",
    "1mo": "daily_ohlcv",
    "1q": "daily_ohlcv",
}

# _timescaledb_functions.cagg_watermark() returns microseconds since
# epoch as a bigint, with a negative sentinel for never-materialized
# caggs. Observed sentinels include MIN_INT64 (-9223372036854775808)
# and -210866803200000000 (microsecond rep of '4714-11-24 BC').
# psycopg's TimestamptzLoader cannot decode any pre-AD-1 timestamptz,
# so any watermark below 0001-01-01 UTC is treated as the sentinel.
# Bound = microseconds from epoch to 0001-01-01 00:00:00 UTC.
_CAGG_WATERMARK_MIN_VALID_US: int = -62135596800000000


@caggs_app.command("status")
def caggs_status(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Show status of all continuous aggregates.

    Surfaces per-cagg refresh-policy installation, last successful policy
    run, next scheduled run, schedule interval, and a staleness signal
    (source MAX(time) − materialized MAX(time_bucket)).
    """
    import psycopg

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)

    # timescaledb_information.jobs.hypertable_name carries the *view*
    # name for cagg refresh policies (not the materialization
    # hypertable name). last_successful_finish + last_run_status live
    # in job_stats, joined by job_id.
    meta_sql = """
        SELECT
            ca.view_name,
            j.job_id,
            js.last_successful_finish,
            j.next_start,
            j.schedule_interval,
            js.last_run_status
        FROM timescaledb_information.continuous_aggregates ca
        LEFT JOIN timescaledb_information.jobs j
            ON j.hypertable_name = ca.view_name
            AND j.proc_name = 'policy_refresh_continuous_aggregate'
        LEFT JOIN timescaledb_information.job_stats js
            ON js.job_id = j.job_id
        ORDER BY ca.view_name
    """

    # `mat_hypertable_id` is needed to call cagg_watermark(); the
    # watermark is the upper-exclusive bound of materialized data, in
    # microseconds since epoch.
    mat_id_sql = """
        SELECT user_view_name, mat_hypertable_id
        FROM _timescaledb_catalog.continuous_agg
    """

    rows: list[dict[str, object]] = []
    with psycopg.connect(settings.timescale_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(meta_sql)
            meta = cur.fetchall()

            cur.execute(mat_id_sql)
            mat_id_by_view: dict[str, int] = {v: i for v, i in cur.fetchall()}

            # MAX("time") on a hypertable with thousands of chunks needs
            # a literal time bound for chunk exclusion; without it the
            # planner scans every chunk. Two-step: latest chunk's
            # range_start (cheap), then bounded MAX.
            source_max: dict[str, object] = {}
            for source in set(_CAGG_SOURCE_TABLE.values()):
                cur.execute(
                    "SELECT MAX(range_start) FROM timescaledb_information.chunks "
                    "WHERE hypertable_name = %s",
                    (source,),
                )
                cutoff = cur.fetchone()[0]
                if cutoff is None:
                    source_max[source] = None
                    continue
                cur.execute(
                    f'SELECT MAX("time") FROM {source} WHERE "time" >= %s',  # noqa: S608 — table name is from a closed enum
                    (cutoff,),
                )
                source_max[source] = cur.fetchone()[0]

            view_to_token = {v: g for g, v in _ALL_CAGG_VIEWS}
            for (
                view_name,
                job_id,
                last_success,
                next_start,
                schedule,
                last_run_status,
            ) in meta:
                token = view_to_token.get(view_name)
                source_table = _CAGG_SOURCE_TABLE.get(token) if token else None
                src_latest = source_max.get(source_table) if source_table else None

                mat_id = mat_id_by_view.get(view_name)
                if mat_id is None:
                    mat_latest = None
                else:
                    # cagg_watermark returns microseconds since epoch as
                    # bigint, or MIN_INT64 (== '-infinity'::timestamptz)
                    # for a never-materialized cagg. Decoding the latter
                    # via to_timestamp() returns '4714-11-23 BC' which
                    # psycopg's TimestamptzLoader cannot represent —
                    # branch on the raw value before converting.
                    cur.execute(
                        "SELECT _timescaledb_functions.cagg_watermark(%s)",
                        (mat_id,),
                    )
                    raw_us = cur.fetchone()[0]
                    if raw_us is None or raw_us < _CAGG_WATERMARK_MIN_VALID_US:
                        mat_latest = None
                    else:
                        cur.execute(
                            "SELECT to_timestamp(%s / 1000000.0)",
                            (raw_us,),
                        )
                        mat_latest = cur.fetchone()[0]

                # cagg_watermark is the upper-exclusive bucket boundary
                # of materialized data. For a fully-current cagg it can
                # exceed the source's latest bar (the next bucket
                # boundary is in the future relative to the latest bar).
                # Clamp negative lag to zero — caller cares about
                # "behind by N", not bucket arithmetic.
                if src_latest is None or mat_latest is None:
                    lag: object = None
                else:
                    raw = src_latest - mat_latest
                    lag = raw if raw.total_seconds() > 0 else "current"

                rows.append({
                    "view": view_name,
                    "granularity": token,
                    "policy_installed": job_id is not None,
                    "last_success": last_success,
                    "last_run_status": last_run_status,
                    "next_start": next_start,
                    "schedule": schedule,
                    "source_latest": src_latest,
                    "mat_latest": mat_latest,
                    "lag": lag,
                })

    if json_output:
        data = [
            {
                "view": r["view"],
                "granularity": r["granularity"],
                "policy_installed": r["policy_installed"],
                "last_success": str(r["last_success"]) if r["last_success"] else None,
                "last_run_status": r["last_run_status"],
                "next_start": str(r["next_start"]) if r["next_start"] else None,
                "schedule": str(r["schedule"]) if r["schedule"] else None,
                "source_latest": str(r["source_latest"]) if r["source_latest"] else None,
                "mat_latest": str(r["mat_latest"]) if r["mat_latest"] else None,
                "lag": str(r["lag"]) if r["lag"] is not None else None,
            }
            for r in rows
        ]
        print_result(data, json_mode=True)
        return

    table = make_table(
        "Continuous Aggregate Status",
        [
            ("View", "bold"),
            ("Gran", ""),
            ("Policy", ""),
            ("Status", ""),
            ("Last Success", ""),
            ("Next Start", ""),
            ("Mat Latest", ""),
            ("Lag", ""),
        ],
    )
    for r in rows:
        table.add_row(
            str(r["view"]),
            str(r["granularity"]) if r["granularity"] else "—",
            "yes" if r["policy_installed"] else "NO",
            str(r["last_run_status"]) if r["last_run_status"] else "—",
            str(r["last_success"])[:19] if r["last_success"] else "—",
            str(r["next_start"])[:19] if r["next_start"] else "—",
            str(r["mat_latest"])[:19] if r["mat_latest"] else "—",
            str(r["lag"]) if r["lag"] is not None else "—",
        )
    print_result(table, json_mode=False)
    print_result(f"\n{len(rows)} aggregate(s)", json_mode=False)


# ---------------------------------------------------------------------------
# caggs verify / repair (slice 163) — minute-cagg parity + restructuring sweep
# ---------------------------------------------------------------------------

# The standing operational rule surfaced in both verify's and repair's help so
# an operator reading either learns it (design D5). Single source of truth.
_CAGG_MAINTENANCE_STANDING_RULE: str = (
    "STANDING RULE: after ANY raw minute_ohlcv chunk restructuring "
    "(e.g. `mt data rechunk`), run `mt data caggs verify`; if parity fails, "
    "run `mt data caggs repair` (rebuilds only the invalidated windows)."
)

_EXIT_PARITY_FAILURE: int = 2
"""caggs verify exit code when any cagg is out of parity (script detector)."""


def _resolve_minute_granularities(
    granularity_opt: str, *, json_output: bool
) -> tuple[Granularity, ...]:
    """Resolve a --granularity option to minute-cagg Granularity values.

    Accepts ``all`` (the four minute caggs) or a comma-separated subset of
    ``5m,15m,1h,4h``. Daily caggs are out of scope for parity/repair. Exits
    non-zero (via typer) on an unknown or non-minute token.
    """
    from manta_trading.constants import (
        MINUTE_CAGG_GRANULARITIES,
        Granularity,
    )

    if granularity_opt.strip().lower() == "all":
        return MINUTE_CAGG_GRANULARITIES

    valid_minute = ", ".join(g.value for g in MINUTE_CAGG_GRANULARITIES)
    requested = [t.strip() for t in granularity_opt.split(",") if t.strip()]
    resolved: list[Granularity] = []
    for token in requested:
        try:
            gran = Granularity(token)
        except ValueError:
            print_error(
                f"Unknown granularity token '{token}'. Valid: all, {valid_minute}",
                json_mode=json_output,
            )
            raise typer.Exit(1) from None
        if gran not in MINUTE_CAGG_GRANULARITIES:
            print_error(
                f"Granularity '{token}' is not a minute cagg. "
                f"Valid: all, {valid_minute}",
                json_mode=json_output,
            )
            raise typer.Exit(1)
        resolved.append(gran)
    if not resolved:
        print_error(
            f"No granularity selected. Valid: all, {valid_minute}",
            json_mode=json_output,
        )
        raise typer.Exit(1)
    # Preserve the canonical smallest-first order regardless of input order.
    return tuple(g for g in MINUTE_CAGG_GRANULARITIES if g in resolved)


@caggs_app.command("verify")
def caggs_verify(
    ctx: typer.Context,
    granularity_opt: str = typer.Option(
        "all",
        "--granularity",
        help="Minute cagg(s) to verify: all | 5m,15m,1h,4h (default all).",
    ),
    detail: bool = typer.Option(
        False,
        "--detail",
        help="Report per 70-day window instead of the per-year rollup.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Verify minute-cagg parity against the raw table (read-only).

    Compares each cagg's SUM(minute_count) to the raw COUNT(*) over 70-day
    epoch-grid windows and reports per-year (or per-window with --detail)
    coverage and parity, plus each cagg's chunk count and interval. Exits with
    a non-zero code if ANY cagg is out of parity, so it doubles as a scriptable
    detector for the self-hiding under-materialization corruption class.

    Every query runs under an explicit statement_timeout and cancels its
    server-side backend on interrupt. See the standing rule below.
    """
    from manta_trading.market.maintenance.cagg_parity import (
        WindowParity,
        compute_parity,
    )

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(1)

    granularities = _resolve_minute_granularities(
        granularity_opt, json_output=json_output
    )

    reports = compute_parity(settings.timescale_db_url, granularities)
    any_failure = any(not r.in_parity for r in reports)

    if json_output:
        payload = []
        for r in reports:
            if detail:
                report_rows: list[dict] = [
                    {
                        "start": w.start.isoformat(),
                        "end": w.end.isoformat(),
                        "raw": w.raw_count,
                        "cagg": w.cagg_count,
                        "coverage": round(w.coverage, 4),
                        "parity": w.parity.value,
                    }
                    for w in r.windows
                ]
            else:
                report_rows = [
                    {
                        "year": y.year,
                        "raw": y.raw_count,
                        "cagg": y.cagg_count,
                        "coverage": round(y.coverage, 4),
                        "parity": y.parity.value,
                    }
                    for y in r.years
                ]
            payload.append({
                "granularity": r.granularity.value,
                "view": r.view_name,
                "raw_total": r.raw_total,
                "cagg_total": r.cagg_total,
                "in_parity": r.in_parity,
                "chunk_count": r.chunk_summary.chunk_count,
                "chunk_interval": (
                    str(r.chunk_summary.chunk_interval)
                    if r.chunk_summary.chunk_interval is not None
                    else None
                ),
                "rows": report_rows,
            })
        print_result(payload, json_mode=True)
        raise typer.Exit(_EXIT_PARITY_FAILURE if any_failure else 0)

    for r in reports:
        interval = (
            str(r.chunk_summary.chunk_interval)
            if r.chunk_summary.chunk_interval is not None
            else "—"
        )
        overall = r.cagg_total / r.raw_total if r.raw_total else 0.0
        header = (
            f"{r.granularity.value} ({r.view_name}) — "
            f"{r.chunk_summary.chunk_count} chunks @ {interval} — "
            f"overall coverage {overall * 100:.1f}% — "
            f"{'PARITY' if r.in_parity else 'PARITY FAILURE'}"
        )
        print_result(header, json_mode=False)

        if detail:
            table = make_table(
                "",
                [("Window start", ""), ("Raw", ""), ("Cagg", ""),
                 ("Coverage", ""), ("Parity", "")],
            )
            for w in r.windows:
                table.add_row(
                    str(w.start)[:10],
                    f"{w.raw_count:,}",
                    f"{w.cagg_count:,}",
                    f"{w.coverage * 100:.1f}%",
                    "ok" if w.parity is WindowParity.DONE else "FAIL",
                )
        else:
            table = make_table(
                "",
                [("Year", ""), ("Raw", ""), ("Cagg", ""),
                 ("Coverage", ""), ("Parity", "")],
            )
            for y in r.years:
                table.add_row(
                    str(y.year),
                    f"{y.raw_count:,}",
                    f"{y.cagg_count:,}",
                    f"{y.coverage * 100:.1f}%",
                    "ok" if y.parity is WindowParity.DONE else "FAIL",
                )
        print_result(table, json_mode=False)

    print_result(f"\n{_CAGG_MAINTENANCE_STANDING_RULE}", json_mode=False)
    if any_failure:
        print_error(
            "Parity failure detected — run `mt data caggs repair`.",
            json_mode=False,
        )
    raise typer.Exit(_EXIT_PARITY_FAILURE if any_failure else 0)


_EXIT_REPAIR_PREFLIGHT: int = 1
"""caggs repair exit code when pre-flight refuses (jobs unpaused, wrong
interval, headroom not attested) or the DB URL is missing."""

_EXIT_REPAIR_FAILED: int = 2
"""caggs repair exit code when a window rebuild fails mid-sweep."""

_EXIT_REPAIR_INTERRUPTED: int = 130
"""caggs repair exit code on Ctrl-C (128 + SIGINT); resume by re-running."""


@caggs_app.command("repair")
def caggs_repair(
    ctx: typer.Context,
    granularity_opt: str = typer.Option(
        "all",
        "--granularity",
        help="Minute cagg to repair: 5m | 15m | 1h | 4h. A real run repairs "
        "exactly ONE cagg per invocation ('all' and comma lists are refused "
        "with the recommended run order); all/multi are allowed with "
        "--dry-run, which is read-only.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print planned windows and per-window parity states; mutate nothing.",
    ),
    assume_headroom_gb: float | None = typer.Option(
        None,
        "--assume-headroom-gb",
        help="Attested free GB on the DB volume (pre-flight cannot read disk "
        "from SQL; required for a real run).",
    ),
) -> None:
    """Re-materialize under-materialized / re-chunked minute caggs (slice 163).

    For the selected cagg, over 70-day epoch-grid windows oldest→newest: skip
    windows already at parity, else drop_chunks → refresh_continuous_aggregate
    (force) → compress. Rebuilds ONLY the windows a restructuring invalidated,
    so the same command is the standing heal after any raw rechunk.

    ONE CAGG PER RUN: a real repair targets exactly one granularity. The 4h
    cagg is both a repair target and the daemon coverage-index source, so
    pre-flight cannot be satisfied for all four caggs at once — repair 4h
    first (own policies paused), resume its refresh policy and run the
    catch-up refresh (runbook R2), then repair 1h, 15m, 5m with the 4h cagg
    back in service. `--dry-run` may still take all/multi (read-only).

    PRE-FLIGHT (refuses, does not warn): the target cagg's refresh policy AND
    columnstore policy must be paused (job IDs printed if not); the 4h
    coverage cagg's refresh policy must be RUNNING when the target is any
    other cagg; migration 044 must be applied (70-day mat interval); disk
    headroom must be attested via --assume-headroom-gb. Raw-table jobs are
    never touched; the daemon may keep running.

    AVAILABILITY: during each window's drop→refresh interval, consumers of that
    cagg see zero coverage for that one 70-day window (seconds-to-minutes).
    Already-repaired and trailing windows stay served. Run outside market hours
    for zero serving impact.

    RESUMABILITY: safe to Ctrl-C mid-window — the server-side backend is
    cancelled and the next run resumes via each window's parity check (state is
    parity-derived, not transactional).

    STANDING RULE: after ANY raw minute_ohlcv restructuring, run
    `mt data caggs verify`; if parity fails, run this command.

    \b
    Exit codes:
      0    success (or dry run)
      1    DB URL missing, or pre-flight refused
      2    a window rebuild failed
      130  interrupted (Ctrl-C) — re-run to resume
    """
    import psycopg as _psycopg

    from manta_trading.market.maintenance.cagg_repair import (
        REPAIR_RUN_ORDER,
        RepairError,
        run_repair,
    )
    from manta_trading.market.maintenance.rechunk import PreflightError

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=False)
        raise typer.Exit(_EXIT_REPAIR_PREFLIGHT)

    granularities = _resolve_minute_granularities(granularity_opt, json_output=False)

    if not dry_run and len(granularities) != 1:
        # An all-cagg (or multi-cagg) real sweep cannot satisfy pre-flight:
        # the 4h cagg must be paused to repair it but running while any other
        # cagg repairs (it feeds the daemon coverage index). One cagg per run,
        # sequenced by the operator.
        order = " -> ".join(g.value for g in REPAIR_RUN_ORDER)
        print_error(
            "A real repair run targets exactly ONE granularity "
            "(--granularity 5m|15m|1h|4h). Recommended sequence: "
            f"{order} — repair 4h first, resume its refresh policy and run "
            "the catch-up refresh (runbook R2), then repair the rest. "
            "Use --dry-run to inspect the all-cagg plan read-only. "
            "See user/runbooks/cagg-maintenance-pausing.md",
            json_mode=False,
        )
        raise typer.Exit(_EXIT_REPAIR_PREFLIGHT)

    try:
        result = run_repair(
            settings.timescale_db_url,
            granularities,
            dry_run=dry_run,
            assume_headroom_gb=assume_headroom_gb,
            progress=lambda msg: print(msg, flush=True),
        )
    except PreflightError as exc:
        print_error(f"Pre-flight refused: {exc}", json_mode=False)
        raise typer.Exit(_EXIT_REPAIR_PREFLIGHT) from exc
    except RepairError as exc:
        print_error(f"Repair failed: {exc}", json_mode=False)
        raise typer.Exit(_EXIT_REPAIR_FAILED) from exc
    except KeyboardInterrupt:
        print_error(
            "Interrupted — backend cancelled. Re-run `mt data caggs repair` "
            "to resume (completed windows are skipped via parity).",
            json_mode=False,
        )
        raise typer.Exit(_EXIT_REPAIR_INTERRUPTED) from None
    except _psycopg.OperationalError as exc:
        print_error(f"Database error: {exc}", json_mode=False)
        raise typer.Exit(_EXIT_REPAIR_FAILED) from exc

    mode = "DRY RUN — no changes made" if result.dry_run else "complete"
    print_result(f"\nCagg repair {mode}.", json_mode=False)
    if not result.dry_run:
        # The pre-flight required the target's refresh + columnstore policies
        # paused; nothing resumes them automatically, and an unresumed
        # columnstore policy leaves late-sweep chunks uncompressed while an
        # unresumed refresh policy strands the trailing edge (review F008).
        print_result(
            "\nNEXT: resume this cagg's paused refresh and columnstore "
            "policies (alter_job(<id>, scheduled => true)); if the refresh "
            "policy was paused longer than its start_offset, run the catch-up "
            "refresh_continuous_aggregate over the paused span. "
            "See user/runbooks/cagg-maintenance-pausing.md (R2, R4).",
            json_mode=False,
        )
        print_result(f"\n{_CAGG_MAINTENANCE_STANDING_RULE}", json_mode=False)

