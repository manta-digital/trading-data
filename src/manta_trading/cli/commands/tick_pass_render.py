"""Rendering for ``mt data tick adopt``, ``reset`` and ``pass`` (223; 224).

Rich tables in text mode, ``cli.output``'s JSON otherwise. Kept apart from
``tick.py`` so the verbs and their exit codes stay under ~300 lines.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from rich import print as rprint
from rich.markup import escape

from manta_trading.cli.commands.tick_render import human_bytes, usd
from manta_trading.cli.output import make_table, print_result
from manta_trading.data.tick.adopt import AdoptResult
from manta_trading.data.tick.manifest_reads import UnitRow
from manta_trading.data.tick.pass_contract import (
    SKIPPED,
    PassResult,
    PhaseReport,
    TickPassPhaseName,
)
from manta_trading.data.tick.reset import ResetChange
from manta_trading.data.tick.spend_guard import usd4


def adopt_to_dict(result: AdoptResult) -> dict[str, Any]:
    return {
        "job_id": result.job_id,
        "already_adopted": result.already_adopted,
        "reverified": result.reverified,
        "cost_usd": None if result.cost_usd is None else str(result.cost_usd),
        "files": [
            {"name": f.name, "size": f.size, "sha256": f.sha256, "copied": f.copied}
            for f in result.files
        ],
        "units_by_state": result.units_by_state,
        "holes": [day.isoformat() for day in result.holes],
        "strays": list(result.strays),
        "verify_failures": list(result.verify_failures),
    }


def print_adopt(result: AdoptResult, *, json_mode: bool) -> None:
    if json_mode:
        print_result(adopt_to_dict(result), json_mode=True)
        return
    if result.already_adopted:
        done = (
            f"verified the {result.reverified} unit(s) left unverified"
            if result.reverified
            else "nothing written"
        )
        print_result(f"{result.job_id}: already adopted; {done}.", json_mode=False)
        _print_verify_failures(result)
        return
    copied = sum(f.copied for f in result.files)
    size = sum(f.size for f in result.files)
    cost = "—" if result.cost_usd is None else usd(result.cost_usd)
    rprint(
        f"[bold]{result.job_id}[/bold]  cost {cost}\n"
        f"Files verified: {len(result.files)} ({human_bytes(size)}), "
        f"{copied} copied, {len(result.files) - copied} already archived"
    )
    table = make_table("Units", [("State", "cyan"), ("Count", "")])
    for state, count in sorted(result.units_by_state.items()):
        table.add_row(state, str(count))
    print_result(table, json_mode=False)
    holes = ", ".join(day.isoformat() for day in result.holes) or "none"
    strays = ", ".join(result.strays) or "none"
    rprint(f"Holes: {holes}\nStray files (no session day): {strays}")
    _print_verify_failures(result)


def _print_verify_failures(result: AdoptResult) -> None:
    for failure in result.verify_failures:
        rprint(f"[red]Verification failed: {failure}[/red]")


def _unit_dict(unit: UnitRow | None) -> dict[str, Any] | None:
    if unit is None:
        return None
    return {
        "state": unit.state.value,
        "fetch_status": unit.fetch_status.value,
        "attempt_count": unit.attempt_count,
        "failure_reason": unit.failure_reason,
        "reopened_at": None
        if unit.reopened_at is None
        else unit.reopened_at.isoformat(),
    }


def reset_to_dict(changes: list[ResetChange]) -> dict[str, Any]:
    return {
        "changes": [
            {
                "unit_id": c.unit_id,
                "action": c.action.value,
                "before": _unit_dict(c.before),
                "after": _unit_dict(c.after),
            }
            for c in changes
        ]
    }


def _cell(unit: UnitRow | None) -> str:
    if unit is None:
        return "—"
    reopened = "" if unit.reopened_at is None else ", reopened"
    return (
        f"{unit.state.value} / {unit.fetch_status.value} "
        f"(attempts {unit.attempt_count}{reopened})"
    )


def print_reset(changes: list[ResetChange], *, json_mode: bool) -> None:
    if json_mode:
        print_result(reset_to_dict(changes), json_mode=True)
        return
    if not changes:
        print_result("No unit to reset or reopen.", json_mode=False)
        return
    table = make_table(
        "Tick reset",
        [("Unit", "cyan"), ("Action", "bold"), ("Before", ""), ("After", "")],
    )
    for change in changes:
        table.add_row(
            str(change.unit_id),
            change.action.value,
            _cell(change.before),
            _cell(change.after),
        )
    print_result(table, json_mode=False)


# -- the pass report (224) ------------------------------------------------------


def pass_to_dict(result: PassResult, exit_code: int) -> dict[str, Any]:
    """``--json``: the pass result plus the exit code the CLI is about to use."""
    return {**result.to_dict(), "exit_code": exit_code}


def _money(value: str | None) -> str:
    """``usd4`` (``usd`` would show a fraction of a cent as ``<$0.01``) of a
    summary's decimal string. ``None`` is an unset ceiling."""
    return "unset" if value is None else usd4(Decimal(value))


def _present(summary: dict[str, Any], labels: dict[str, str]) -> list[str]:
    return [
        f"{label}: {summary[key]}" for key, label in labels.items() if key in summary
    ]


_DELIVERY_LABELS = {
    "polled": "jobs polled",
    "delivered": "delivered",
    "expired": "expired",
    "refused": "refused states",
    "downloaded": "units downloaded",
    "verified": "verified",
    "holed": "holes",
    "failed": "failed",
}


def _in_flight_lines(summary: dict[str, Any]) -> list[str]:
    return [
        f"in flight: {job['job_id']} ({job['state']}), deadline "
        f"{job['deadline'] or 'not yet known'}"
        for job in summary.get("in_flight", [])
    ]


def _reconcile_lines(summary: dict[str, Any]) -> list[str]:
    lines = []
    if "unknown_submits" in summary:
        matched = summary["unknown_submits"]
        lines.append(
            "unknown submits: "
            + ", ".join(f"{n} {name}" for name, n in matched.items())
        )
    lines += _present(summary, {"swept_expired": "swept as expired"})
    lines += _present(summary, _DELIVERY_LABELS) + _in_flight_lines(summary)
    return lines


def _availability_lines(summary: dict[str, Any]) -> list[str]:
    lines = []
    for dataset, found in summary.items():
        span = "no span" if found["span"] is None else " → ".join(found["span"])
        tally = ", ".join(f"{n} {name}" for name, n in found["conditions"].items())
        lines.append(
            f"{dataset}: edge {found['edge_end']}; days {span}; "
            f"{tally or 'no conditions'}; {found['written']} written, "
            f"{found['reopened']} reopened"
        )
    return lines


def _purchase_lines(summary: dict[str, Any]) -> list[str]:
    if "verdict" not in summary:
        return []
    lines = []
    for key, wanted in summary["wanted_days"].items():
        pending = summary["pending_days"].get(key, 0)
        missing = summary["missing_days"].get(key, 0)
        lines.append(
            f"wanted {key}: {wanted} day(s), {pending} pending, {missing} missing"
        )
    lines += [
        f"request {r['schema']} {r['start']} → {r['end']} ({r['days']} day(s)) "
        f"{_money(r['cost_usd'])}{' [re-submit]' if r['resubmit'] else ''}"
        for r in summary["requests"]
    ]
    lines.append(
        f"planned {_money(summary['planned_usd'])}; trailing 30 days "
        f"{_money(summary['trailing_30d_usd'])}; per-pass ceiling "
        f"{_money(summary['per_pass_ceiling_usd'])}; 30-day cap "
        f"{_money(summary['cap_30d_usd'])}"
    )
    lines += [
        f"unheld provider job {job['job_id']} {_money(job['cost_usd'])}"
        for job in summary["unheld_jobs"]
    ]
    lines.append(f"verdict: {summary['verdict']}")
    lines += [f"  {reason}" for reason in summary["reasons"]]
    lines += [
        f"submitted job {job['job_id']} (request {job['request_id']})"
        for job in summary.get("submitted", [])
    ]
    return lines


def _await_lines(summary: dict[str, Any]) -> list[str]:
    if "skipped" in summary:
        return [f"skipped ({summary['skipped']})"]
    labels = {"waited_seconds": "seconds waited", **_DELIVERY_LABELS}
    return _present(summary, labels) + _in_flight_lines(summary)


def _definitions_lines(summary: dict[str, Any]) -> list[str]:
    return _present(
        summary,
        {
            "projected": "units projected",
            "inserted": "rows inserted",
            "noops": "no-ops",
            "failed": "failures",
        },
    )


_PHASE_LINES: dict[TickPassPhaseName, Callable[[dict[str, Any]], list[str]]] = {
    TickPassPhaseName.RECONCILE: _reconcile_lines,
    TickPassPhaseName.AVAILABILITY: _availability_lines,
    TickPassPhaseName.PURCHASE: _purchase_lines,
    TickPassPhaseName.AWAIT: _await_lines,
    TickPassPhaseName.DEFINITIONS: _definitions_lines,
}
assert set(_PHASE_LINES) == set(TickPassPhaseName), (
    "the pass report has no summary lines for a phase — update _PHASE_LINES"
)


def _print_phase(report: PhaseReport) -> None:
    if report.outcome == SKIPPED:
        return
    rprint(f"[bold]{report.name}[/bold]")
    for line in _PHASE_LINES[report.name](report.summary):
        rprint(f"  {escape(line)}")
    if report.error:
        rprint(f"  [red]{escape(report.error)}[/red]")


def print_pass(result: PassResult, exit_code: int, *, json_mode: bool) -> None:
    if json_mode:
        print_result(pass_to_dict(result, exit_code), json_mode=True)
        return
    table = make_table(
        "Tick pass", [("Phase", "cyan"), ("Outcome", "bold"), ("Duration", "")]
    )
    for report in result.reports:
        table.add_row(str(report.name), str(report.outcome), f"{report.duration_ms} ms")
    print_result(table, json_mode=False)
    for report in result.reports:
        _print_phase(report)
    rprint(
        f"Outcome: {result.outcome}  exit {exit_code}  duration {result.duration_ms} ms"
    )
