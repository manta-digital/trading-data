"""Rich and JSON rendering for ``mt data tick ingest`` (slice 225).

LLD 225 API Contracts: one line per unit, the skip tally and the totals.
``--json`` is ``PassResult.to_dict()`` with ``exit_code``, as for ``pass``.
"""

from __future__ import annotations

from typing import Any

from rich import print as rprint
from rich.markup import escape

from manta_trading.cli.commands.tick_pass_render import pass_to_dict
from manta_trading.cli.output import print_result
from manta_trading.data.tick.ingest_select import SkipKind
from manta_trading.data.tick.pass_contract import PassResult, PhaseReport

_SKIP_LABELS = {kind: kind.value.replace("_", " ") for kind in SkipKind}


def _unit_line(unit: dict[str, Any]) -> str:
    head = f"unit {unit['unit_id']}"
    if "unit_date" in unit:
        head += f"  {unit['unit_date']} {unit['schema']}  {unit['records']} rec"
    line = f"{head}  → {unit['outcome']}"
    if "duration_seconds" in unit:
        line += f"  ({unit['duration_seconds']} s)"
    if unit.get("reason"):
        line += f"  {unit['reason']}"
    return line


def _phase_lines(report: PhaseReport) -> list[str]:
    summary = report.summary
    lines = [_unit_line(unit) for unit in summary.get("units", [])]
    if "skipped" in summary:
        skipped = ", ".join(
            f"{summary['skipped'][kind.value]} {label}"
            for kind, label in _SKIP_LABELS.items()
        )
        lines.append(f"skipped: {skipped}")
        lines.append(
            f"ingested {summary['ingested']} units, {summary['records']} records;"
            f" failed {summary['failed']}"
        )
    if report.error:
        lines.append(f"[red]{escape(report.error)}[/red]")
    return lines


def print_ingest(
    result: PassResult, exit_code: int, workers: int, *, json_mode: bool
) -> None:
    if json_mode:
        print_result(pass_to_dict(result, exit_code), json_mode=True)
        return
    rprint(f"ingest  run {str(result.run_id)[:8]}…  {workers} workers")
    for report in result.reports:
        for line in _phase_lines(report):
            rprint(f"  {line if line.startswith('[red]') else escape(line)}")
    rprint(f"outcome {result.outcome}  (exit {exit_code})")
