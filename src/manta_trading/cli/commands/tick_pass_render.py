"""Rendering for ``mt data tick adopt`` and ``reset`` (223; 224 adds ``pass``).

Rich tables in text mode, ``cli.output``'s JSON otherwise. Kept apart from
``tick.py`` so the verbs and their exit codes stay under ~300 lines.
"""

from __future__ import annotations

from typing import Any

from rich import print as rprint

from manta_trading.cli.commands.tick_render import human_bytes, usd
from manta_trading.cli.output import make_table, print_result
from manta_trading.data.tick.adopt import AdoptResult
from manta_trading.data.tick.manifest_reads import UnitRow
from manta_trading.data.tick.reset import ResetChange


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
