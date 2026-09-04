#!/usr/bin/env python3
"""Cut slice 268 (trades-tape category filter) over to production on manta9000.

Runs after the release carrying slice 268 is installed on the host (ordinary
release workflow — not this script's job). One command that performs the
walkthrough's step 7 between the Project Manager's two acts: deciding to cut
over before, and reading the report after.

    uv run python scripts/cutover_268_trades_filter.py

Steps
  1. PRECONDITION (design 268, Decision 8): abort unless
     `mt-run data kalshi status --json` shows the historical tape at floor
     reached — the live catch-up range skips filtered trades from the moment
     the variable is set, and that skip is not recoverable by unsetting.
  2. set the variable     MT_KALSHI_TRADES_EXCLUDED_CATEGORIES=Crypto in
                          /etc/manta-trading.env (backup kept; idempotent —
                          an existing line with the same value is left alone)
  3. restart the service  fire the kalshi pass unit once, supervised, so the
                          journal carries a post-cutover start line
  4. report               the three walkthrough step-7 checks, each printed:
                          the status filter line, the journal start line's
                          `trades filter` entry, and
                          `trades.filter.tape_filtered_markets > 0` (the
                          named post-cutover typo check)

Safe to re-run: every step is check-then-act and the env edit is idempotent.
It touches only the environment file and the pass service unit. Exit status
is 0 only when every check passed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from manta_trading.config import KALSHI_TRADES_FILTER_ENV

# Host facts — the same single source deploy/install-production.sh uses.
ENV_FILE = Path("/etc/manta-trading.env")
PASS_UNIT = "mt-kalshi-pass.service"
#: The PM's production intent (design 268, *Configuration*).
FILTER_VALUE = "Crypto"
ENV_LINE = f"{KALSHI_TRADES_FILTER_ENV}={FILTER_VALUE}"
#: The one spelling of the filter description (selection.describe_trades_filter).
EXPECTED_DESCRIPTION = f"excluding {FILTER_VALUE}"
START_LINE_ENTRY = f"trades filter: {EXPECTED_DESCRIPTION}"
POLL_SECONDS = 15


class CutoverError(RuntimeError):
    """A step found the host in a state it will not act on."""


def say(text: str) -> None:
    print(f"\n==> {text}", flush=True)


def run(
    args: list[str], *, sudo: bool = False, check: bool = True, stream: bool = False
) -> subprocess.CompletedProcess[str]:
    cmd = (["sudo", *args]) if sudo else args
    return subprocess.run(cmd, check=check, text=True, capture_output=not stream)


def out(args: list[str], *, sudo: bool = False) -> str:
    return run(args, sudo=sudo).stdout.strip()


def unit_active(unit: str) -> bool:
    state = run(["systemctl", "is-active", unit], check=False).stdout.strip()
    return state in {"active", "activating"}


def wait_for_pass_to_end() -> None:
    while unit_active(PASS_UNIT):
        print(f"    a Kalshi pass is running — waiting ({POLL_SECONDS}s) …", flush=True)
        time.sleep(POLL_SECONDS)


def production_status_json() -> dict:
    raw = out(["mt-run", "data", "kalshi", "status", "--json"], sudo=True)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def precondition_floor_reached() -> None:
    """Decision 8: never enable the filter while the backfill is still
    descending — the abort happens before anything is touched."""
    status = production_status_json()
    historical = status.get("historical") or {}
    if historical.get("floor_reached") is not True:
        raise CutoverError(
            "PRECONDITION failed (design 268, Decision 8): the historical tape "
            f"is not at floor reached — historical={json.dumps(historical)}. "
            "Nothing was changed; re-run once `mt data kalshi status` shows "
            "the floor."
        )
    print(f"    historical floor reached (floor {historical.get('floor')}) — ok")


def write_env(text: str) -> None:
    subprocess.run(
        ["sudo", "tee", str(ENV_FILE)],
        input=text,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def journal_cursor() -> str:
    text = out(["journalctl", "-u", PASS_UNIT, "-n", "0", "--show-cursor", "-q"])
    match = re.search(r"-- cursor: (\S+)", text)
    if not match:
        raise CutoverError(f"could not read a journal cursor from: {text!r}")
    return match.group(1)


def fire_pass() -> str:
    """Restart the pass unit once, supervised, and return the journal cursor
    taken before it — the post-cutover start line is what the report reads."""
    wait_for_pass_to_end()
    cursor = journal_cursor()
    print("    sudo mt-run kalshi — streaming; the filter reads from the env file")
    run(["mt-run", "kalshi"], sudo=True, check=False, stream=True)
    wait_for_pass_to_end()
    run(["sudo", "-v"], stream=True)  # the firing may outlive the sudo grace
    return cursor


def journal_after(cursor: str) -> str:
    return out(
        [
            "journalctl",
            "-u",
            PASS_UNIT,
            f"--after-cursor={cursor}",
            "--no-pager",
            "-q",
        ]
    )


def apply_env_line() -> None:
    """Idempotent env edit: the exact line present → no change; a commented
    or different-valued line is replaced in place; otherwise appended beside
    the other Kalshi lines. Backup kept on every actual edit."""
    content = out(["cat", str(ENV_FILE)], sudo=True)  # values never printed
    lines = content.splitlines()
    pattern = re.compile(rf"^\s*#?\s*{KALSHI_TRADES_FILTER_ENV}\s*=")
    active = [
        line
        for line in lines
        if pattern.match(line) and not line.lstrip().startswith("#")
    ]
    if active == [ENV_LINE]:
        print(f"    {ENV_LINE} already set — nothing to do")
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = f"{ENV_FILE}.bak-268-{stamp}"
    run(["cp", "-p", str(ENV_FILE), backup], sudo=True)
    replaced = False
    edited: list[str] = []
    for line in lines:
        if pattern.match(line) and not replaced:
            edited.append(ENV_LINE)
            replaced = True
        elif pattern.match(line):
            continue  # drop duplicate occurrences; one line owns the value
        else:
            edited.append(line)
    if not replaced:
        edited.append(ENV_LINE)
    write_env("\n".join(edited) + "\n")
    verify = out(["grep", "-c", f"^{ENV_LINE}$", str(ENV_FILE)], sudo=True)
    if verify != "1":
        raise CutoverError(
            f"{ENV_FILE} does not carry exactly one `{ENV_LINE}` line after the "
            f"edit (found {verify}); backup at {backup}"
        )
    print(f"    {ENV_LINE} written (backup {backup})")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def report(cursor: str) -> bool:
    checks: list[tuple[bool, str]] = []

    status_text = out(["mt-run", "data", "kalshi", "status"], sudo=True)
    filter_lines = [
        " ".join(line.split())
        for line in status_text.splitlines()
        if "trades filter" in line
    ]
    ok = any(EXPECTED_DESCRIPTION in line for line in filter_lines)
    checks.append(
        (ok, f"status filter line: {filter_lines or '(no trades filter line)'}")
    )

    journal = journal_after(cursor)
    ok = START_LINE_ENTRY in journal
    checks.append(
        (
            ok,
            f"journal start line carries {START_LINE_ENTRY!r}: {ok}",
        )
    )

    status = production_status_json()
    trades_filter = (status.get("trades") or {}).get("filter") or {}
    count = trades_filter.get("tape_filtered_markets")
    ok = isinstance(count, int) and count > 0
    checks.append(
        (
            ok,
            "trades.filter.tape_filtered_markets > 0 (the named typo check): "
            f"{trades_filter}",
        )
    )

    print()
    for ok, text in checks:
        print(f"    {'✅' if ok else '❌'} {text}")
    return all(ok for ok, _ in checks)


# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    run(["sudo", "-v"], stream=True)
    say("1/4 PRECONDITION: historical floor reached (Decision 8)")
    precondition_floor_reached()
    say(f"2/4 set {ENV_LINE} in {ENV_FILE}")
    apply_env_line()
    say("3/4 fire the pass once, supervised")
    cursor = fire_pass()
    say("4/4 report — walkthrough step 7 checks")
    all_ok = report(cursor)
    print(
        "\n    Note (observation, not a gate): WAL rate and /data growth are "
        "expected to drop toward the 5-15 GB/day steady state over subsequent "
        "days."
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except CutoverError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(
            f"\nERROR: {' '.join(exc.cmd)} exited {exc.returncode}\n{exc.stderr or ''}",
            file=sys.stderr,
        )
        sys.exit(1)
