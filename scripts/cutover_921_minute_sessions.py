#!/usr/bin/env python
"""Cut slice 921 over on manta9000 and prove it, in one command.

Slice 921 Task 7.3. The whole cutover is this script: install the release,
repair the six weeks of truncated sessions, fire the passes that refetch them,
and print the acceptance numbers. Nothing here asks the Project Manager to run
a step by hand or to come back tomorrow — the measurements are in this run's
own report.

**Timing.** Run whenever there is provider budget — the script checks and
tells you. The repair itself needs no quota, but the firings it triggers do.
When the daily allowance is the only source, that means running just after
its 00:00 UTC reset; when extra calls are in the account (``extraLimit``),
the reset does not matter and any time is fine. The preflight prints the
remaining balance and refuses below ``MIN_CREDITS_TO_START`` rather than
letting the passes produce acceptance numbers that measure a starved
account instead of the fix.

**Why the minute pass fires more than once.** The trailing phase takes ONE
chunk per symbol per cycle (file 1, Task 3.4) and picks the newest actionable
gap, so a symbol the repair left with several in-window ranges needs several
passes. This loops until ``--verify`` reports no pending trailing work,
bounded by the quota: a QUOTA_EXHAUSTED outcome ends the loop and is reported,
never retried.

Check-then-act throughout, and every action is logged — this runs with root
privileges and must be auditable afterwards.

    sudo -v && uv run python scripts/cutover_921_minute_sessions.py --ref v0.14.0
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cutover_common import (  # noqa: E402
    NOTES_DIR,
    fire_unit_until,
    start_log,
    CutoverError,
    fire_unit,
    install,
    preflight,
    read_unit_journal,
    run,
    say,
    unit_active,
    unit_result,
)

MINUTE_UNIT = "mt-minute-pass.service"
MINUTE_TIMER = "mt-minute-pass.timer"
DAILY_UNIT = "mt-daily-pass.service"
DAILY_TIMER = "mt-daily-pass.timer"

REPAIR_SCRIPT = "scripts/repair_921_minute_sessions.py"

#: Credits the cutover wants available before it fires anything.
#:
#: The trailing phase costs ~65,000 for the full active universe (13,083
#: symbols x 1 chunk x EODHD_INTRADAY_CALL_COST), and the acceptance numbers
#: are meaningless if the passes 402 partway through. Half again on top leaves
#: room for the daily pass and some backfill. Below this the script refuses
#: and says what it found, rather than producing a report that measures a
#: spent allowance.
MIN_CREDITS_TO_START = 100_000

#: Ceiling on minute firings. The trailing phase is one chunk per symbol, so
#: several passes may be needed; this bounds a pathological loop. Reaching it
#: is reported as a FAIL, not retried past.
MAX_MINUTE_FIRINGS = 6

#: --verify answers WAIT while the health cagg's hourly refresh has not caught
#: up with the firing's bars (#22). Poll it this often, this many times — the
#: refresh policy runs every hour, so five 15-minute polls always span one.
VERIFY_WAIT_SECONDS = 15 * 60
VERIFY_WAIT_POLLS = 5

#: Journal markers the firings are read back for (SC6).
TRAILING_COMPLETE = re.compile(r"trailing phase complete: (\d+) symbols")
PASS_ABORTED = re.compile(r"minute pass aborted: (\S+)")


def _repair(mode: str) -> str:
    """Run the repair script through the production interpreter, streamed."""
    say(f"  running {REPAIR_SCRIPT} {mode}")
    result = run(
        ["/opt/manta-trading/.venv/bin/python", REPAIR_SCRIPT, mode],
        sudo=True,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if result.returncode not in (0,):
        say(f"  {mode} exited {result.returncode}")
    return output


def _remaining_credits() -> int:
    """Remaining EODHD credits: daily allowance minus used, plus extras."""
    import httpx

    from manta_trading.config import Settings
    from manta_trading.constants import HEALTH_EODHD_USER_ENDPOINT

    settings = Settings()
    response = httpx.get(
        HEALTH_EODHD_USER_ENDPOINT,
        params={"api_token": settings.eodhd_api_key, "fmt": "json"},
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    return (
        int(body["dailyRateLimit"]) - int(body["apiRequests"]) + int(body["extraLimit"])
    )


def _refuse_without_budget() -> None:
    """Check-then-act on the provider allowance.

    The firings are what produce the acceptance numbers; running them against
    a spent account measures the starvation, not the fix.
    """
    remaining = _remaining_credits()
    say(f"  EODHD credits remaining: {remaining:,}")
    if remaining < MIN_CREDITS_TO_START:
        raise CutoverError(
            f"only {remaining:,} EODHD credits remain, below the "
            f"{MIN_CREDITS_TO_START:,} this cutover wants. Either wait for the "
            "00:00 UTC daily reset or add extra calls to the account."
        )


def _refuse_if_a_pass_is_running() -> None:
    """Check-then-act: never write while an acquisition pass holds the rows."""
    for unit in (MINUTE_UNIT, DAILY_UNIT):
        if unit_active(unit):
            raise CutoverError(
                f"{unit} is active — stop it (or wait) before cutting over"
            )
    say("  no acquisition pass is running")


def _hold_timers() -> list[str]:
    """Stop the acquisition timers so nothing fires mid-cutover. Returns the
    ones that were active, for release afterwards."""
    held: list[str] = []
    for timer in (MINUTE_TIMER, DAILY_TIMER):
        if unit_active(timer):
            run(["systemctl", "stop", timer], sudo=True)
            held.append(timer)
            say(f"  {timer} stopped for the cutover")
        else:
            say(f"  {timer} was already inactive")
    return held


def _release_timers(held: list[str]) -> None:
    for timer in held:
        run(["systemctl", "start", timer], sudo=True)
        say(f"  {timer} started again")


def _fire_minute_pass(index: int) -> tuple[bool, str | None]:
    """One minute firing. Returns (trailing phase completed, abort outcome)."""
    say(f"  minute firing {index}")
    # #22: a firing is unbounded (backfill runs until quota); the cutover
    # needs only its trailing phase, so the unit is stopped once that line
    # lands and the timers own the backfill.
    cursor, _started, _reached = fire_unit_until(
        MINUTE_UNIT, ["minute"], TRAILING_COMPLETE
    )
    firing = read_unit_journal(cursor, MINUTE_UNIT)

    trailing = firing.first(TRAILING_COMPLETE)
    if trailing:
        say(f"    trailing phase complete: {trailing.group(1)} symbols")
    else:
        say("    NO trailing-phase completion line in the journal")

    abort = firing.first(PASS_ABORTED)
    if abort:
        say(f"    pass aborted: {abort.group(1)}")

    result, status = unit_result(MINUTE_UNIT)
    say(f"    unit result={result} exit={status}")
    return trailing is not None, abort.group(1) if abort else None


def _verify_when_fresh() -> str:
    """--verify, re-polled while it reports WAIT (#22); returns the last text."""
    text = _repair("--verify")
    polls = 0
    while "[WAIT]" in text and polls < VERIFY_WAIT_POLLS:
        polls += 1
        say(
            f"  verify is waiting for the cagg refresh — poll {polls}/"
            f"{VERIFY_WAIT_POLLS} in {VERIFY_WAIT_SECONDS // 60} min"
        )
        time.sleep(VERIFY_WAIT_SECONDS)
        text = _repair("--verify")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="Git ref to install.")
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="The release is already installed; go straight to the repair.",
    )
    args = parser.parse_args(argv)

    say("slice 921 cutover — minute acquisition correctness")
    log_path = start_log(
        NOTES_DIR / f"921-cutover-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.log"
    )
    say(f"  narration is written to {log_path}")

    say("preflight")
    commit = preflight(args.ref)
    say(f"  {args.ref} is {commit[:12]}")
    _refuse_if_a_pass_is_running()
    _refuse_without_budget()

    held = _hold_timers()
    try:
        if not args.skip_install:
            say("install")
            install(args.ref, commit)

        say("before-image")
        _repair("--check")

        say("repair")
        _repair("--apply")

        say("after-image")
        _repair("--check")

        say("fire the daily pass")
        cursor, _ = fire_unit(DAILY_UNIT, ["daily"])
        daily = read_unit_journal(cursor, DAILY_UNIT)
        say(f"  {len(daily.entries)} journal lines")

        say("fire the minute pass until the trailing work is drained")
        aborted: str | None = None
        for index in range(1, MAX_MINUTE_FIRINGS + 1):
            _completed, aborted = _fire_minute_pass(index)
            if aborted:
                say(f"  stopping: the pass reported {aborted}")
                break
            verify_text = _verify_when_fresh()
            if "FAIL" not in verify_text and "[WAIT]" not in verify_text:
                say(f"  every acceptance bar met after {index} firing(s)")
                break
        else:
            say(f"  reached the {MAX_MINUTE_FIRINGS}-firing ceiling")

        say("acceptance")
        report = _verify_when_fresh()
        ok = "FAIL" not in report and "[WAIT]" not in report and aborted is None
    finally:
        _release_timers(held)

    say("CUTOVER COMPLETE" if ok else "CUTOVER INCOMPLETE — read the FAIL lines")
    say(f"  full narration: {log_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CutoverError as exc:
        print(f"cutover refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
