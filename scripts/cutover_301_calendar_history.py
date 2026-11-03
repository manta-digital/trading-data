#!/usr/bin/env python3
"""Put the slice 301 release into production: CME calendar history.

    uv run python scripts/cutover_301_calendar_history.py <release-tag>

The release carries minute migration ``066_cme_calendar_history``, which fills
``CME_EQUITY`` and ``CME_METALS`` back to ``CME_DATASET_START`` and proves every
session from 2020 on unchanged. Run as the operator from the dev checkout root
with the release checked out. Every step is check-then-act; after a failure,
fix the cause and re-run.

Steps
  1. preflight      ref exists here and on origin
  2. hold timers    stop each acquisition timer, then wait out its pass
  3. install        deploy/install-production.sh --ref <ref>
  4. migrate        minute to 066 (target verified)
  5. restart serve  mt-serve onto the new code
  6. verify         both CME calendars' first session is the first on or after
                    CME_DATASET_START; mt data health passes; mt data overview
                    exits 0
  7. release timers started again, as found (skipped after a failed migrate;
                    a rerun after the fix releases them)

The narration goes to project-documents/user/notes/; exit 0 only when every
check passed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    NOTES_DIR,
    SERVE_UNIT,
    CutoverError,
    install,
    log_text,
    migrate,
    out,
    preflight,
    restart_serve_if_active,
    run,
    say,
    start_log,
    unit_active,
)
from upgrade_production import hold_timers, release_timers  # noqa: E402

from manta_trading.market.schema.seed_cme_hours import (  # noqa: E402
    CME_DATASET_START,
)

MINUTE_MIGRATION = ("minute", "066_cme_calendar_history")
CME_CALENDARS = ("CME_EQUITY", "CME_METALS")
FIRST_WEEK = timedelta(days=7)
"""How far past the dataset start to list sessions when finding the first."""


def first_session_expected() -> date:
    """The first weekday on or after ``CME_DATASET_START``."""
    day = CME_DATASET_START
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def check_first_session(calendar_id: str, sessions_json: str) -> date:
    """The calendar's first listed session; raise unless it is the expected one."""
    # Log lines may share the stream; the rows are the array starting at column 0.
    lines = sessions_json.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("[")), None)
    if start is None:
        raise CutoverError(f"{calendar_id}: no JSON rows in {sessions_json[-500:]}")
    rows = json.loads("\n".join(lines[start:]))
    if not rows:
        raise CutoverError(f"{calendar_id}: no session in the dataset's first week")
    first = date.fromisoformat(rows[0]["session_date"])
    if first != first_session_expected():
        raise CutoverError(
            f"{calendar_id}: first session {first}, expected {first_session_expected()}"
        )
    return first


def verify_first_sessions() -> None:
    for calendar_id in CME_CALENDARS:
        raw = out(
            [
                "mt-run", "data", "calendars", "sessions",
                "--calendar", calendar_id,
                # The first weekday, not the dataset start: it falls on a Sunday,
                # and the calendar is populated from that evening's open, so a
                # midnight-local --from on it is outside the calendar.
                "--from", first_session_expected().isoformat(),
                "--to", (first_session_expected() + FIRST_WEEK).isoformat(),
                "--json",
            ],
            sudo=True,
        )  # fmt: skip
        log_text(raw)
        first = check_first_session(calendar_id, raw)
        print(f"    {calendar_id} first session {first}")


def verify_command(args: list[str]) -> None:
    result = run(["mt-run", *args], sudo=True, check=False)
    log_text(result.stdout or "")
    if result.returncode != 0:
        log_text(result.stderr or "")
        raise CutoverError(f"mt {' '.join(args)} exited {result.returncode}")
    print(f"    mt {' '.join(args)} passes")


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    ref = argv[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log = start_log(NOTES_DIR / f"cutover-{ref}-{stamp}.log")
    print(f"narration -> {log}")

    held: list[str] = []
    release = True
    try:
        say(f"1/7 preflight — {ref}")
        commit = preflight(ref)

        say("2/7 hold the acquisition timers")
        hold_timers(held)

        say(f"3/7 install {ref}")
        version = install(ref, commit)
        if ref.lstrip("v") not in version:
            raise CutoverError(f"installed mt reports {version!r}, expected {ref}")

        say("4/7 migrate")
        release = False  # a half-migrated host keeps its timers stopped
        migrate(*MINUTE_MIGRATION)
        release = True

        say(f"5/7 restart {SERVE_UNIT}")
        restart_serve_if_active()
        if not unit_active(SERVE_UNIT):
            raise CutoverError(f"{SERVE_UNIT} is not active after the restart")

        say("6/7 verify")
        verify_first_sessions()
        verify_command(["data", "health"])
        verify_command(["data", "overview"])
    except (CutoverError, subprocess.CalledProcessError, OSError) as exc:
        say(f"FAILED: {exc}")
        if not release:
            say(f"timers left stopped after a failed migrate: {held}")
        return 1
    finally:
        if release:
            say("7/7 release the timers")
            release_timers(held)

    say(f"PASS — production is at {ref} ({commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
