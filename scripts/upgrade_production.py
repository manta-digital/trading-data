#!/usr/bin/env python3
"""Upgrade production to a release that needs no migration.

    uv run python scripts/upgrade_production.py v0.19.0

For a code-only release: no pending migration on the production database and
no new unit to enable. A release that needs either still gets its own cutover
script (see ``cutover_922_overview.py``). This script checks for pending
migrations after the install, and if it finds any it stops without
restarting anything.

Run it as the operator from the dev checkout root, with the release checked
out, so the track list comes from the release being installed. Every step is
check-then-act; after a failure, fix the cause and re-run.

Steps
  1. preflight      ref exists here and on origin
  2. hold timers    stop each acquisition timer, then wait out its pass
  3. install        deploy/install-production.sh --ref <ref>; /opt at the
                    ref's commit, installed mt reports the ref's version
  4. migrations     every primary-database track reports 0 pending through
                    the production front door; otherwise stop, timers held
  5. restart serve  mt-serve onto the new code
  6. verify         mt data overview exits 0 through the production binary
  7. release timers started again, as found (skipped if step 4 stopped)

The report goes to project-documents/user/notes/; exit 0 only when every
check passed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    NOTES_DIR,
    SERVE_UNIT,
    CutoverError,
    install,
    log_text,
    out,
    preflight,
    restart_serve_if_active,
    run,
    say,
    start_log,
    unit_active,
    wait_for_unit_to_end,
)

from manta_trading.market.schema.databases import Database  # noqa: E402
from manta_trading.market.schema.migrations import TRACK_REGISTRY  # noqa: E402

ACQUISITION_UNITS = (
    ("mt-minute-pass.service", "mt-minute-pass.timer"),
    ("mt-daily-pass.service", "mt-daily-pass.timer"),
    ("mt-kalshi-pass.service", "mt-kalshi-pass.timer"),
    ("mt-health.service", "mt-health.timer"),
    ("mt-accounting-pass.service", "mt-accounting-pass.timer"),
)
"""Held for the install: a pass must not start while the venv is rebuilt."""

PRIMARY_TRACKS = tuple(
    name for name, spec in TRACK_REGISTRY.items() if spec.database is Database.PRIMARY
)


def hold_timers() -> list[str]:
    """Stop each active timer first, then wait out its pass; return those held."""
    held: list[str] = []
    for service, timer in ACQUISITION_UNITS:
        if unit_active(timer):
            run(["systemctl", "stop", timer], sudo=True)
            held.append(timer)
            print(f"    {timer} stopped for the upgrade")
        else:
            print(f"    {timer} was not active — leaving it as found")
        wait_for_unit_to_end(service)
    return held


def release_timers(held: list[str]) -> None:
    for timer in held:
        run(["systemctl", "start", timer], sudo=True, check=False)
        print(f"    {timer} started again (Persistent=true may fire at once)")


def pending_migrations() -> dict[str, list[str]]:
    """Pending ids per primary track, read through the production front door."""
    pending: dict[str, list[str]] = {}
    for track in PRIMARY_TRACKS:
        raw = out(
            ["mt-run", "data", "migrate", "status", "--track", track, "--json"],
            sudo=True,
        )
        payload = json.loads(raw)
        if not payload.get("connected"):
            raise CutoverError(f"migrate status ({track}): {payload.get('error')}")
        pending[track] = [entry["id"] for entry in payload["pending"]]
    return pending


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    ref = argv[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log = start_log(NOTES_DIR / f"upgrade-{ref}-{stamp}.log")
    print(f"narration -> {log}")

    held: list[str] = []
    release = True
    try:
        say(f"1/7 preflight — {ref}")
        commit = preflight(ref)

        say("2/7 hold the acquisition timers")
        held = hold_timers()

        say(f"3/7 install {ref}")
        version = install(ref, commit)
        if ref.lstrip("v") not in version:
            raise CutoverError(f"installed mt reports {version!r}, expected {ref}")

        say("4/7 pending migrations on the primary database")
        pending = pending_migrations()
        if any(pending.values()):
            release = False
            raise CutoverError(
                f"pending migrations {pending}: this release needs a cutover "
                f"script. Timers left stopped: {held}"
            )
        print(f"    0 pending on {', '.join(PRIMARY_TRACKS)}")

        say(f"5/7 restart {SERVE_UNIT}")
        restart_serve_if_active()
        if not unit_active(SERVE_UNIT):
            raise CutoverError(f"{SERVE_UNIT} is not active after the restart")

        say("6/7 verify")
        result = run(["mt-run", "data", "overview"], sudo=True, check=False)
        log_text(result.stdout or "")
        if result.returncode != 0:
            log_text(result.stderr or "")
            raise CutoverError(f"mt data overview exited {result.returncode}")
        print("    mt data overview renders")
    except (CutoverError, subprocess.CalledProcessError) as exc:
        say(f"FAILED: {exc}")
        return 1
    finally:
        if release:
            say("7/7 release the timers")
            release_timers(held)

    say(f"PASS — production is at {ref} ({commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
