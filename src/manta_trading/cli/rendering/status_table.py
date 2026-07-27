"""Rich rendering helpers for mt data status (slice 147 Decision B, F).

Pure functions: take dataclasses, return Rich renderables or strings.
No DB access. No Typer dependency.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta, timezone
from enum import Enum, StrEnum
from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table

from manta_trading.data.maintenance.auto_extend import AutoExtendResult
from manta_trading.data.maintenance.status_coverage import CoverageFreshness

# ---------------------------------------------------------------------------
# Health and fetch-status constants
# ---------------------------------------------------------------------------


class HealthStatus(StrEnum):
    OK = "OK"
    GAPS = "GAPS"
    STALE = "STALE"
    FAILED = "FAILED"


_HEALTH_COLORS: dict[str, str] = {
    HealthStatus.OK: "green",
    HealthStatus.GAPS: "yellow",
    HealthStatus.STALE: "blue",
    HealthStatus.FAILED: "red",
}

_FETCH_STATUS_COLORS: dict[str, str] = {
    "UNKNOWN": "dim",
    "PROVIDER_HOLE": "blue",
    "FAILED_RETRYABLE": "yellow",
    "RETRY_EXHAUSTED": "red",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class StatusRow:
    """One row from the data_status view."""

    symbol: str
    granularity: str
    health: str
    bars_stored: int | None
    first_bar_ts: datetime | None
    last_bar_ts: datetime | None
    gap_count: int | None
    last_attempt_ts: datetime | None
    last_attempt_outcome: str | None
    target_end_ts: datetime | None
    effective_start: date | None


@dataclasses.dataclass
class GapRow:
    """One row from data_gaps (filtered to a specific symbol)."""

    symbol: str
    granularity: str
    gap_start: datetime
    gap_end: datetime
    fetch_status: str
    attempt_count: int
    last_attempt_ts: datetime | None


@dataclasses.dataclass
class StatusReport:
    """Full output of a status query."""

    scope: str
    symbol: str | None
    rows: list[StatusRow]
    gaps: list[GapRow]
    auto_extend: AutoExtendResult | None
    summary: dict[str, int]
    coverage: CoverageFreshness | None = None
    """Freshness of the caggs behind ``bars_summary`` (slice 167 D6).

    Defaulted to None so callers that do not read ``data_status`` through the
    guarded accessor still construct a valid report; None renders as no banner
    and a null JSON key, never as "fresh".
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _humanize_ts(ts: datetime | None) -> str:
    """Return 'never' for None; relative 'Xm ago' / 'Xh ago' / 'Xd ago' for past."""
    if ts is None:
        return "never"
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "in the future"
    minutes = total_seconds // 60
    hours = minutes // 60
    days = hours // 24
    if days >= 1:
        return f"{days}d ago"
    if hours >= 1:
        return f"{hours}h ago"
    return f"{minutes}m ago"


def _health_color(health: str) -> str:
    """Return Rich markup color for a health string."""
    return _HEALTH_COLORS.get(health, "white")


def _fetch_status_color(fetch_status: str) -> str:
    return _FETCH_STATUS_COLORS.get(fetch_status, "white")


def _fmt_date(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _fmt_int(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _make_granularity_table(title: str, rows: list[StatusRow]) -> Table:
    tbl = Table(title=title, show_header=True, header_style="bold")
    tbl.add_column("symbol")
    tbl.add_column("health")
    tbl.add_column("bars", justify="right")
    tbl.add_column("first_bar")
    tbl.add_column("last_bar")
    tbl.add_column("gaps", justify="right")
    tbl.add_column("last_attempt")
    tbl.add_column("outcome")

    for row in sorted(rows, key=lambda r: r.symbol):
        color = _health_color(row.health)
        tbl.add_row(
            row.symbol,
            f"[{color}]{row.health}[/{color}]",
            _fmt_int(row.bars_stored),
            _fmt_date(row.first_bar_ts),
            _fmt_date(row.last_bar_ts),
            _fmt_int(row.gap_count),
            _humanize_ts(row.last_attempt_ts),
            row.last_attempt_outcome or "—",
        )
    return tbl


def render_status_summary(report: StatusReport) -> list[Table]:
    """Return one Rich Table per granularity present in the report (daily first, then minute)."""
    tables: list[Table] = []
    for gran in ("daily", "minute"):
        gran_rows = [r for r in report.rows if r.granularity == gran]
        if gran_rows:
            tables.append(_make_granularity_table(f"Data Status — {gran}", gran_rows))
    # Any unexpected granularity values get their own table at the end.
    known = {"daily", "minute"}
    other_grans = sorted({r.granularity for r in report.rows if r.granularity not in known})
    for gran in other_grans:
        gran_rows = [r for r in report.rows if r.granularity == gran]
        tables.append(_make_granularity_table(f"Data Status — {gran}", gran_rows))
    return tables


def render_status_footer(report: StatusReport, *, all_rows: bool = False) -> str:
    """Format the OK/GAPS/STALE/FAILED summary line.

    When all_rows=True, appends an advisory line (Decision C).
    """
    parts = [
        f"{label}: {report.summary.get(label, 0)}"
        for label in (
            HealthStatus.OK,
            HealthStatus.GAPS,
            HealthStatus.STALE,
            HealthStatus.FAILED,
        )
    ]
    line = "  ".join(parts)
    if all_rows:
        n = len(report.rows)
        line += f"\n{n:,} rows printed; use `--health` or `--symbol` to filter."
    return line


def render_status_detail(report: StatusReport) -> list[RenderableType]:
    """Return list of Rich renderables for the symbol-detail view."""
    renderables: list[RenderableType] = []

    for row in report.rows:
        color = _health_color(row.health)
        lines = [
            f"[bold]symbol:[/bold]       {row.symbol}",
            f"[bold]granularity:[/bold]  {row.granularity}",
            f"[bold]health:[/bold]       [{color}]{row.health}[/{color}]",
            f"[bold]bars_stored:[/bold]  {_fmt_int(row.bars_stored)}",
            f"[bold]first_bar:[/bold]    {_fmt_date(row.first_bar_ts)}",
            f"[bold]last_bar:[/bold]     {_fmt_date(row.last_bar_ts)}",
            f"[bold]gap_count:[/bold]    {_fmt_int(row.gap_count)}",
            f"[bold]last_attempt:[/bold] {_humanize_ts(row.last_attempt_ts)}",
            f"[bold]outcome:[/bold]      {row.last_attempt_outcome or '—'}",
        ]
        panel = Panel("\n".join(lines), title=f"{row.symbol} / {row.granularity}")
        renderables.append(panel)

    if report.gaps:
        gap_tbl = Table(title="data_gaps", show_header=True, header_style="bold")
        gap_tbl.add_column("granularity")
        gap_tbl.add_column("gap_start")
        gap_tbl.add_column("gap_end")
        gap_tbl.add_column("fetch_status")
        gap_tbl.add_column("attempts", justify="right")
        gap_tbl.add_column("last_attempt")

        sorted_gaps = sorted(report.gaps, key=lambda g: g.gap_start)
        for gap in sorted_gaps:
            fs_color = _fetch_status_color(gap.fetch_status)
            gap_tbl.add_row(
                gap.granularity,
                _fmt_date(gap.gap_start),
                _fmt_date(gap.gap_end),
                f"[{fs_color}]{gap.fetch_status}[/{fs_color}]",
                str(gap.attempt_count),
                _humanize_ts(gap.last_attempt_ts),
            )
        renderables.append(gap_tbl)

    return renderables


def render_coverage_notice(coverage: CoverageFreshness | None) -> str | None:
    """Return a stale-coverage warning, or None when coverage is fresh/unknown.

    Deliberately a banner rather than a column: the ``data_status`` column
    contract is fixed (slice 167 D2), and freshness describes the whole
    ``bars_summary`` CTE rather than any single row.

    Rendered *above* the tables by the caller. Stale coverage understates bar
    counts and last_bar timestamps, so an operator has to see it before reading
    the numbers, not after scrolling past them.
    """
    if coverage is None or not coverage.is_stale:
        return None
    stale_names = ", ".join(v.view_name for v in coverage.stale_verdicts)
    return (
        f"[yellow]Warning:[/yellow] coverage is STALE ({stale_names}). "
        "bars, first_bar and last_bar may understate reality; gap and attempt "
        "columns are unaffected.\n"
        f"  {coverage.describe()}\n"
        "  Coverage catch-up is runbook R2; `mt data caggs verify` for detail."
    )


def render_auto_extend_notice(result: AutoExtendResult) -> str | None:
    """Return a notice string when triggered or on error; None if no-op."""
    if result.error is not None:
        calendars = ", ".join(result.calendars_extended) or "one or more calendars"
        return (
            f"[yellow]Warning:[/yellow] Auto-extend failed for {calendars}; "
            "run `mt data --extend` manually."
        )
    if result.triggered:
        cal_list = ", ".join(result.calendars_extended)
        horizons = ", ".join(
            f"{cal}→{d}" for cal, d in result.horizon_after.items()
        )
        return (
            f"Auto-extended trading_sessions for {cal_list}: "
            f"{result.rows_inserted} rows inserted ({horizons})."
        )
    return None


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    # Coverage freshness (slice 167) carries lag/threshold as timedelta and
    # signals as StalenessSignal; both reach here via the nested dataclass.
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def status_report_to_json(report: StatusReport) -> str:
    """Serialize StatusReport to JSON string.

    Dates/datetimes → ISO-8601 strings; None → JSON null.
    auto_extend.error is included as a field when present.

    ``coverage`` is a sibling of ``rows``, never a per-row field — the column
    contract is fixed (slice 167 D2) and freshness is a property of the whole
    result. ``is_stale`` is emitted explicitly because it is a property rather
    than a field, so ``asdict`` would otherwise drop it and force consumers to
    re-derive staleness by scanning verdicts.
    """
    d = dataclasses.asdict(report)
    if report.coverage is not None:
        d["coverage"]["is_stale"] = report.coverage.is_stale
    return json.dumps(d, default=_json_default, indent=2)
