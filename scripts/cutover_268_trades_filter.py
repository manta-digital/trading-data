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
from datetime import UTC, datetime

from cutover_common import (
    ENV_FILE,
    CutoverError,
    fire,
    out,
    production_status,
    read_journal,
    run,
    say,
    wait_for_pass_to_end,
)

from manta_trading.config import KALSHI_TRADES_FILTER_ENV

#: The PM's production intent (design 268, *Configuration*).
FILTER_VALUE = "Crypto"
ENV_LINE = f"{KALSHI_TRADES_FILTER_ENV}={FILTER_VALUE}"
#: The one spelling of the filter description (selection.describe_trades_filter).
EXPECTED_DESCRIPTION = f"excluding {FILTER_VALUE}"
START_LINE_ENTRY = f"trades filter: {EXPECTED_DESCRIPTION}"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def precondition_floor_reached() -> None:
    """Decision 8: never enable the filter while the backfill is still
    descending — the abort happens before anything is touched. Also proves
    the installed release carries slice 268: the pre-268 binary silently
    ignores the env var (pydantic ``extra="ignore"``), so firing it would
    store filtered categories and fail every report check."""
    status = production_status()
    trades = status.get("trades") or {}
    if "filter" not in trades:
        raise CutoverError(
            "the installed release does not carry slice 268: `mt-run data "
            "kalshi status --json` has no trades.filter block, so the running "
            "binary would silently ignore the variable. Install the release "
            "on /opt/manta-trading first (ordinary release workflow), then "
            "re-run. Nothing was changed."
        )
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
    firing = read_journal(cursor)

    status_text = out(["mt-run", "data", "kalshi", "status"], sudo=True)
    filter_lines = [
        " ".join(line.split())
        for line in status_text.splitlines()
        if "trades filter" in line
    ]
    firing.check(
        any(EXPECTED_DESCRIPTION in line for line in filter_lines),
        f"status filter line: {filter_lines or '(no trades filter line)'}",
    )

    start_lines = firing.find(START_LINE_ENTRY)
    firing.check(
        bool(start_lines),
        f"journal start line carries {START_LINE_ENTRY!r}: {bool(start_lines)}",
    )

    trades_filter = (production_status().get("trades") or {}).get("filter") or {}
    count = trades_filter.get("tape_filtered_markets")
    firing.check(
        isinstance(count, int) and count > 0,
        "trades.filter.tape_filtered_markets > 0 (the named typo check): "
        f"{trades_filter}",
    )

    print()
    for ok, text in firing.checks:
        print(f"    {'✅' if ok else '❌'} {text}")
    return firing.all_ok


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
    wait_for_pass_to_end()  # a timer-launched pass holds the run lock
    cursor, _started = fire()
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
