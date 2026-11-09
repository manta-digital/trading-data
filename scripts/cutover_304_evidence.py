"""Evidence collection and the exit rule of the slice 304 cutover (D5, D6, D7).

Every production read goes through ``mt_run`` (``sudo mt-run ...``), so a test
fakes that one function. A command that exits outside its accepted codes stops
the cutover naming the command; failures of the cutover's own judgement are
collected, so the whole report is written before the script exits non-zero.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_304_checks import (  # noqa: E402
    VERDICT_NOTHING,
    FindingClass,
    SessionCheck,
    bar_job_dirs,
    calendar_for,
    classify_unfinished,
    classify_unit,
    run_session_check,
)
from cutover_common import CutoverError, log_text, run, say  # noqa: E402

from manta_trading.data.tick.constants import TickSchema  # noqa: E402
from manta_trading.data.tick.universe import TICK_UNIVERSE  # noqa: E402

RECONCILE_SCRIPT = Path(__file__).resolve().parent / "reconcile_tick_spend.py"
EXIT_OK, EXIT_PARTIAL = 0, 3  # ``mt data tick`` verbs: 3 = a verdict, not a failure
RECONCILE_FAILED, RECONCILE_STOPPED = 1, 2
PRODUCTS = tuple(entry.product for entry in TICK_UNIVERSE)
COMPLETE = "complete"
MISMATCH = "mismatch"
INGESTED = "ingested"
TICK_PASS_KIND = "tick"


def mt_run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """``sudo mt-run <args>``; the caller judges the exit code."""
    return run(["mt-run", *args], sudo=True, check=False)


def mt_json(args: Sequence[str], accepted: Iterable[int] = (EXIT_OK,)) -> Any:
    """Run ``mt-run <args> --json``, log the raw JSON, return it parsed."""
    command = " ".join(args)
    result = mt_run([*args, "--json"])
    log_text(f"$ mt-run {command} --json (exit {result.returncode})\n{result.stdout}")
    if result.returncode not in set(accepted):
        raise CutoverError(
            f"mt-run {command} exited {result.returncode}:"
            f" {(result.stderr or '').strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CutoverError(f"mt-run {command} printed no JSON: {exc}") from exc


def coverage_reads() -> list[tuple[str, str, str, str]]:
    """``(product, schema, start, end)`` for every history want but definitions."""
    reads = []
    for entry in TICK_UNIVERSE:
        for want in entry.history:
            if want.schema is TickSchema.DEFINITION:
                continue
            if want.end is None:
                raise CutoverError(
                    f"{entry.product} {want.schema.value} want has no end date"
                )
            reads.append(
                (
                    entry.product,
                    want.schema.value,
                    want.start.isoformat(),
                    want.end.isoformat(),
                )
            )
    return reads


def health_exit_code() -> int:
    return mt_run(["data", "health"]).returncode


def definition_counts() -> dict[str, int]:
    """``tick_definition`` rows per product (spreads included)."""
    return {
        product: len(
            mt_json(
                [
                    "data",
                    "tick",
                    "debug",
                    "definitions",
                    "--product",
                    product,
                    "--spreads",
                ]
            )["rows"]
        )
        for product in PRODUCTS
    }


def ledger_failures(coverage: Mapping[str, Any], schema: str) -> list[str]:
    """Sessions whose ledger does not equal raw, naming product, schema, date."""
    failures = []
    for product in coverage["products"]:
        for session in product["sessions"]:
            unequal = (
                session["status"] == COMPLETE and session["ledger"] != session["raw"]
            )
            if session["check"] == MISMATCH or unequal:
                failures.append(
                    f"{product['product']} {schema} {session['session_date']}:"
                    f" ledger {session['ledger']} raw {session['raw']}"
                )
    return failures


def unfinished_sessions(coverage: Mapping[str, Any]) -> Counter[str]:
    """Sessions short of ``complete``, tallied by status (listed, not failed)."""
    return Counter(
        session["status"]
        for product in coverage["products"]
        for session in product["sessions"]
        if session["status"] != COMPLETE
    )


def definition_delta(
    before: Mapping[str, int], after: Mapping[str, int]
) -> dict[str, tuple[int, int, int]]:
    return {p: (before[p], after[p], after[p] - before[p]) for p in after}


def pass_durations(overview: Mapping[str, Any]) -> list[str]:
    """The tick pass's last run: outcome and seconds, from ``mt data overview``."""
    lines = []
    for line in overview["passes"]:
        last = line["last_run"]
        if line["pass"] == TICK_PASS_KIND and last is not None:
            seconds = (
                datetime.fromisoformat(last["ended_at"])
                - datetime.fromisoformat(last["started_at"])
            ).total_seconds()
            lines.append(
                f"tick pass {last['outcome']} exit {last['exit_code']}: {seconds:.0f} s"
            )
    return lines


@dataclass
class Evidence:
    failures: list[str] = field(default_factory=list)
    unfinished: Counter[FindingClass] = field(default_factory=Counter)
    unfinal_units: list[int] = field(default_factory=list)


def _reconcile(start: str, cap: Decimal, evidence: Evidence) -> None:
    cmd = [
        sys.executable,
        str(RECONCILE_SCRIPT),
        "--since",
        start,
        "--cap-usd",
        str(cap),
    ]
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    log_text(f"$ reconcile_tick_spend (exit {result.returncode})\n{result.stdout}")
    print(result.stdout)
    if result.returncode:
        log_text(f"reconcile_tick_spend stderr:\n{result.stderr}")
    if result.returncode == RECONCILE_STOPPED:
        raise CutoverError(f"reconcile_tick_spend stopped: {result.stderr.strip()}")
    if result.returncode == RECONCILE_FAILED:
        evidence.failures.append("reconciliation failed (see the narration)")


def _unit_is_final(row: Mapping[str, Any]) -> bool:
    """Ingested, failed with a reason, or in a named D6 class (awaiting definitions)."""
    if row["state"] == INGESTED or row["failure_reason"]:
        return True
    return classify_unit(row) is FindingClass.AWAITING_DEFINITIONS


def collect(start: str, cap: Decimal, baseline: Mapping[str, Any]) -> Evidence:
    """Step 5: every evidence command, narrated; the judgements go in ``failures``."""
    evidence = Evidence()
    say("reconciliation")
    _reconcile(start, cap, evidence)
    say("status and coverage")
    verdict_exits = (EXIT_OK, EXIT_PARTIAL)
    mt_json(["data", "tick", "status"], verdict_exits)
    for product, schema, first, last in coverage_reads():
        args = ["data", "tick", "coverage", "--product", product, "--schema", schema]
        doc = mt_json([*args, "--start", first, "--end", last], verdict_exits)
        evidence.failures += ledger_failures(doc, schema)
        log_text(
            f"{product} {schema} sessions not complete:"
            f" {dict(unfinished_sessions(doc))}"
        )
    say("manifest counts, definitions, durations")
    requests = mt_json(["data", "tick", "debug", "requests"])["rows"]
    units = mt_json(["data", "tick", "debug", "units"])["rows"]
    after = definition_counts()
    say(f"requests {len(requests)}, units {len(units)}")
    say(
        "tick_definition (before, after, delta):"
        f" {definition_delta(baseline['definitions'], after)}"
    )
    for line in pass_durations(mt_json(["data", "overview"])):
        say(line)
    evidence.unfinished = classify_unfinished(units)
    say(f"units not ingested by D6 class: {dict(evidence.unfinished)}")
    evidence.unfinal_units = [
        row["unit_id"] for row in units if not _unit_is_final(row)
    ]
    _session_placement(requests)
    return evidence


def _session_placement(requests: Sequence[Mapping[str, Any]]) -> None:
    dirs_by_product = bar_job_dirs(requests)
    if not dirs_by_product:
        say("session placement: no ohlcv-1m job directories")
        return
    for product, dirs in dirs_by_product.items():
        outcome = run_session_check(dirs, calendar_for(product))
        say(f"session placement {product}: {outcome}")
        if outcome is SessionCheck.FINDING:
            say(f"{product}: records outside any session are a finding; see above")


def final_failures(
    evidence: Evidence, baseline: Mapping[str, Any], second_estimate: Mapping[str, Any]
) -> list[str]:
    """Step 6: the exit rule. Coverage findings and exit 3 never fail the script."""
    failures = list(evidence.failures)
    if evidence.unfinal_units:
        failures.append(
            f"units neither ingested nor failed with a reason: {evidence.unfinal_units}"
        )
    if health_exit_code() != baseline["health_exit"]:
        failures.append(f"health exit changed from {baseline['health_exit']}")
    planned = Decimal(second_estimate["planned_usd"])
    if second_estimate["verdict"] != VERDICT_NOTHING or planned != 0:
        failures.append(
            f"a second estimate still plans {planned} USD:"
            f" {second_estimate['requests']}"
        )
    return failures
