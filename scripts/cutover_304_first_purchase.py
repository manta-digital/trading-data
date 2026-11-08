#!/usr/bin/env python3
"""Slice 304's first real purchase: install the release, buy, ingest, collect evidence.

    uv run python scripts/cutover_304_first_purchase.py <release-tag>

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
    check_checkout_database,
    check_env_text,
    judge_estimate,
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
from upgrade_production import hold_timers, release_timers  # noqa: E402

PASS_ATTEMPTS = 5
EXIT_STILL_PROCESSING = 6
PASS_DONE_EXITS = (0, 3)  # 3: some units failed, which is a finding
TICK_UNIT = "mt-tick-pass.service"
JOURNAL_LINES = 30
START_FILE = NOTES_DIR / "304-cutover-start.txt"
BASELINE_FILE = NOTES_DIR / "304-cutover-baseline.json"
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


def record_start_and_baselines() -> tuple[str, dict[str, Any]]:
    """First run: write the start instant and the baselines. Re-run: read them."""
    if START_FILE.exists() and BASELINE_FILE.exists():
        return START_FILE.read_text().strip(), json.loads(BASELINE_FILE.read_text())
    baseline = {
        "health_exit": evidence.health_exit_code(),
        "definitions": evidence.definition_counts(),
    }
    log_text(f"baselines: {baseline}")
    start = datetime.now(UTC).isoformat(timespec="seconds")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(baseline))
    START_FILE.write_text(start + "\n")
    return start, baseline


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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    ref = argv[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    print(f"narration -> {start_log(NOTES_DIR / f'cutover-304-{ref}-{stamp}.log')}")
    try:
        return _steps(ref)
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


def _steps(ref: str) -> int:
    say(f"1/6 preflight — {ref}")
    commit = preflight(ref)
    production_env = read_env_text()
    per_pass: Decimal = check_env_text(production_env)
    check_checkout_database(read_checkout_env_text(), production_env)
    start, baseline = record_start_and_baselines()
    say(f"2/6 install {ref}")
    install_release(ref, commit)
    say("3/6 estimate")
    if judge_estimate(fetch_estimate()) is Estimate.NOTHING_TO_BUY:
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
