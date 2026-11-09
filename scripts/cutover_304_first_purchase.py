#!/usr/bin/env python3
"""A purchase slice's cutover: install the release, buy, ingest, collect evidence.

    uv run python scripts/cutover_304_first_purchase.py \\
        --slice <n> --gate-issue <n> [--estimate] <release-tag>

Written for slice 304's first purchase and shared by every purchase slice: what
is wanted comes from ``TICK_UNIVERSE``, and ``--slice`` names the state files
(``{slice}-cutover-start.txt``, ``{slice}-cutover-baseline.json``) and the log
(``cutover-{slice}-{tag}-{stamp}.log``) under the notes directory.

Run as the operator from the dev checkout root with the release checked out and
the PM's ceiling script already run (the preflight names its command if not).
Every step is check-then-act and re-runnable; nothing waits on a person.

Steps
  1. preflight    ref exists and is on origin; env file has no plan and both
                  ceilings; start instant and baselines recorded (first run)
  2. install      acquisition timers held, ref installed, timers released as
                  found on every exit from this step (no migration)
  3. estimate     ``pass --estimate-only --json``, judged; refuses before any
                  spend; ``nothing_to_buy`` skips step 4 (a clean re-run)
  4. buy, ingest  ``sudo mt-run tick``, run again while it exits 6
  5. evidence     reconciliation, status, coverage, counts, session placement
  6. checks       health unchanged, second estimate plans nothing, exit rule

The narration goes to project-documents/user/notes/; exit 0 only when the exit
rule holds.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cutover_304_evidence as evidence  # noqa: E402
from cutover_304_checks import (  # noqa: E402
    Estimate,
    ceiling_command,
    check_checkout_database,
    check_env_text,
    check_plan_unset,
    judge_estimate,
    resubmit_origins,
)
from cutover_common import (  # noqa: E402
    ENV_FILE,
    NOTES_DIR,
    CutoverError,
    install,
    log_text,
    out,
    preflight,
    run,
    say,
    start_log,
)
from purchase_verdicts import (  # noqa: E402
    check_dataset_edge,
    check_gate_issue,
    latest_want_end,
)
from upgrade_production import hold_timers, release_timers  # noqa: E402

PASS_ATTEMPTS = 5
EXIT_STILL_PROCESSING = 6
PASS_DONE_EXITS = (0, 3)  # 3: some units failed, which is a finding
TICK_UNIT = "mt-tick-pass.service"
JOURNAL_LINES = 30
EXIT_USAGE = 2
ESTIMATE_ARGS = ["data", "tick", "pass", "--estimate-only", "--json"]


class PassStopped(CutoverError):
    """A pass exited with a code the cutover will not continue past."""

    def __init__(self, exit_code: int) -> None:
        super().__init__(f"mt-run tick exited {exit_code}")
        self.exit_code = exit_code


def read_env_text() -> str:
    return out(["cat", str(ENV_FILE)], sudo=True)


def read_checkout_env_text() -> str:
    return Path(".env").read_text()


def start_file(slice_id: str) -> Path:
    return NOTES_DIR / f"{slice_id}-cutover-start.txt"


def baseline_file(slice_id: str) -> Path:
    return NOTES_DIR / f"{slice_id}-cutover-baseline.json"


def record_start_and_baselines(slice_id: str) -> tuple[str, dict[str, Any]]:
    """First run: write the start instant and the baselines. Re-run: read them."""
    start_path, baseline_path = start_file(slice_id), baseline_file(slice_id)
    if start_path.exists() and baseline_path.exists():
        return start_path.read_text().strip(), json.loads(baseline_path.read_text())
    baseline = {
        "health_exit": evidence.health_exit_code(),
        "definitions": evidence.definition_counts(),
    }
    log_text(f"baselines: {baseline}")
    start = datetime.now(UTC).isoformat(timespec="seconds")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(baseline))
    start_path.write_text(start + "\n")
    return start, baseline


def _view_issue(number: int) -> str:
    return out(["gh", "issue", "view", str(number), "--json", "state"])


def run_gates(gate_issue: int) -> None:
    """The two refusals made before the install, any state or any spend (D1, D2).

    Separate from ``cutover_common.preflight`` so 304's tests can still call that
    with no gate issue.
    """
    check_gate_issue(gate_issue, _view_issue)
    status = evidence.mt_run(["data", "tick", "status", "--json"])
    check_dataset_edge(status, latest_want_end())


def install_release(ref: str, commit: str) -> None:
    """Hold the timers, install; release them as found on every exit."""
    held: list[str] = []
    try:
        hold_timers(held)
        version = install(ref, commit)
        if ref.lstrip("v") not in version:
            raise CutoverError(f"installed mt reports {version!r}, expected {ref}")
    finally:
        release_timers(held)


def fetch_estimate() -> dict[str, Any]:
    """``pass --estimate-only --json``, free; any non-zero exit stops the script."""
    result = evidence.mt_run(ESTIMATE_ARGS)
    log_text(f"$ pass --estimate-only (exit {result.returncode})\n{result.stdout}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CutoverError(f"estimate exited {result.returncode}: {detail}")
    try:
        summary: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CutoverError(f"estimate printed no JSON: {exc}") from exc
    return summary


def judge(
    summary: dict[str, Any], start: str | None, *, check_ceilings: bool = True
) -> Estimate:
    """Judge the estimate; a resubmit is matched to its original request's row."""
    since = None if start is None else datetime.fromisoformat(start)
    origins = {}
    if since is not None and any(req["resubmit"] for req in summary["requests"]):
        rows = evidence.mt_json(["data", "tick", "debug", "requests"])["rows"]
        origins = resubmit_origins(rows)
    return judge_estimate(summary, since, origins, check_ceilings=check_ceilings)


def run_passes(run_pass: Callable[[], int]) -> int:
    """Run ``mt-run tick`` while it exits 6; return its final exit (0 or 3)."""
    for _ in range(PASS_ATTEMPTS):
        code = run_pass()
        if code in PASS_DONE_EXITS:
            return code
        if code != EXIT_STILL_PROCESSING:
            raise PassStopped(code)
    raise CutoverError(
        f"jobs still processing after {PASS_ATTEMPTS} runs; re-run the cutover"
    )


def _tick_once() -> int:
    result = run(["mt-run", "tick"], sudo=True, check=False)
    log_text(f"$ sudo mt-run tick (exit {result.returncode})\n{result.stdout}")
    if result.returncode not in (*PASS_DONE_EXITS, EXIT_STILL_PROCESSING):
        journal = ["journalctl", "-u", TICK_UNIT, "-n", str(JOURNAL_LINES)]
        log_text(run([*journal, "--no-pager"], check=False).stdout)
        log_text(result.stderr or "")
    return result.returncode


def _nonempty(text: str) -> str:
    if not text.strip():
        raise argparse.ArgumentTypeError("must not be empty")
    return text.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cutover_304_first_purchase.py", description=__doc__.split("\n")[0]
    )
    parser.add_argument("ref", type=_nonempty, help="release tag to install")
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="preflight and the free estimate only; print the PM's ceiling command",
    )
    parser.add_argument(
        "--gate-issue",
        required=True,
        type=int,
        help="issue that must be closed first (the invoice comparison); no skip flag",
    )
    parser.add_argument(
        "--slice", required=True, dest="slice_id", type=_nonempty, help="slice number"
    )
    return parser


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv[1:])
    except SystemExit as exc:
        # argparse already printed usage; its exit code (2, or 0 for --help) stands.
        return int(exc.code or 0)
    ref, slice_id = args.ref, args.slice_id
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = NOTES_DIR / f"cutover-{slice_id}-{ref}-{stamp}.log"
    print(f"narration -> {start_log(log_path)}")
    try:
        if args.estimate:
            return _estimate_only(ref, args.gate_issue)
        return _steps(ref, slice_id, args.gate_issue)
    except PassStopped as exc:
        say(f"FAILED: {exc}")
        return exc.exit_code
    except (CutoverError, subprocess.CalledProcessError, OSError) as exc:
        say(f"FAILED: {exc}")
        return 1
    except (KeyError, ValueError) as exc:
        # A production command's payload changed shape: a stop, in the record.
        say(f"FAILED: unexpected payload ({type(exc).__name__}: {exc})")
        return 1


def _estimate_only(ref: str, gate_issue: int) -> int:
    """Preflight and the free estimate: buys nothing, writes no state (D4).

    The ceilings are usually unset here, so their env checks are skipped and a
    short ceiling is reported as the command to run, not as a stop.
    """
    say(f"1/2 preflight — {ref}")
    preflight(ref)
    production_env = read_env_text()
    check_plan_unset(production_env)
    check_checkout_database(read_checkout_env_text(), production_env)
    run_gates(gate_issue)
    say("2/2 estimate")
    summary = fetch_estimate()
    outcome = judge(summary, None, check_ceilings=False)
    planned = Decimal(summary["planned_usd"])
    say(f"planned {planned} USD over {len(summary['requests'])} requests")
    say(f"verdict {summary['verdict']}: {outcome}")
    if outcome is Estimate.BUY:
        trailing = Decimal(summary["trailing_30d_usd"])
        say(f"the PM runs: {ceiling_command(planned, trailing)}")
    return 0


def _steps(ref: str, slice_id: str, gate_issue: int) -> int:
    say(f"1/6 preflight — {ref}")
    commit = preflight(ref)
    production_env = read_env_text()
    per_pass: Decimal = check_env_text(production_env)
    check_checkout_database(read_checkout_env_text(), production_env)
    run_gates(gate_issue)
    start, baseline = record_start_and_baselines(slice_id)
    say(f"2/6 install {ref}")
    install_release(ref, commit)
    say("3/6 estimate")
    if judge(fetch_estimate(), start) is Estimate.NOTHING_TO_BUY:
        say("4/6 nothing to buy; straight to evidence")
    else:
        say("4/6 buy and ingest")
        say(f"mt-run tick ended with exit {run_passes(_tick_once)}")
    say("5/6 evidence")
    found = evidence.collect(start, per_pass, baseline)
    say("6/6 checks")
    failures = evidence.final_failures(found, baseline, fetch_estimate())
    for failure in failures:
        say(f"FAIL: {failure}")
    if failures:
        return 1
    say(f"PASS — first purchase complete at {ref} ({commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
