"""Rich rendering for ``mt data tick estimate`` (slice 220)."""

from __future__ import annotations

from rich import print as rprint

from manta_trading.cli.output import make_table, print_result
from manta_trading.data.tick.estimate import EstimateReport, SchemaEstimate

_BYTE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")
_BYTE_STEP = 1024


def human_bytes(size: int) -> str:
    """Binary-prefixed size: ``96000000`` → ``91.6 MiB``."""
    value = float(size)
    for unit in _BYTE_UNITS[:-1]:
        if value < _BYTE_STEP:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= _BYTE_STEP
    return f"{value:.1f} {_BYTE_UNITS[-1]}"


def _header(report: EstimateReport) -> str:
    request = report.request
    tally = ", ".join(f"{n} {c.value}" for c, n in report.conditions.items() if n)
    ceiling = "unset" if report.ceiling_usd is None else f"${report.ceiling_usd}"
    return (
        f"[bold]{request.dataset}[/bold] {', '.join(request.symbols)} "
        f"({request.stype_in.value})\n"
        f"Available: {report.available.start.isoformat()} → "
        f"{report.available.end.isoformat()}\n"
        f"Requested: {request.start} → {request.end} (end exclusive)\n"
        f"Day conditions: {tally}\n"
        f"Spend ceiling: {ceiling}"
    )


def _cells(row: SchemaEstimate) -> tuple[str, ...]:
    bundle = "—" if row.bundle_cost_usd is None else f"${row.bundle_cost_usd}"
    return (
        row.schema.value,
        f"{row.record_count:,}",
        f"{row.billable_bytes:,}",
        human_bytes(row.billable_bytes),
        f"${row.cost_usd}",
        bundle,
        row.verdict.value,
    )


def print_estimate(report: EstimateReport, *, json_mode: bool) -> None:
    if json_mode:
        print_result(report.to_dict(), json_mode=True)
        return
    table = make_table(
        "Tick cost preflight",
        [
            ("Schema", "cyan"),
            ("Records", ""),
            ("Billable bytes", ""),
            ("Size", ""),
            ("Cost (USD)", ""),
            ("Bundle (tier + definition)", ""),
            ("Verdict", "bold"),
        ],
    )
    for row in report.rows:
        table.add_row(*_cells(row))
    rprint(_header(report))
    print_result(table, json_mode=False)
