#!/usr/bin/env python3
"""Shrink production's daily gap rows after the #83 fix.

    uv run python scripts/cutover_083_daily_gaps.py <release-tag>

The release fixes daily gap detection (bars are stamped 00:00Z on the session
date, sessions open at 14:30Z or 13:30Z, and the old code compared the two as
instants, so every session looked missing). Existing daily gap rows spanning
sessions whose bars are stored are replaced by the sub-ranges that really lack
a bar, keeping each row's status and attempt count. No migration, no provider
call. Run as the operator from the dev checkout root with the release checked
out. Every step is check-then-act; a second reconcile changes nothing.

Steps
  1. preflight      ref exists here and on origin; the checkout's application
                    URL is production's database
  2. dry check      what the reconcile would do (nothing written)
  3. hold timers    stop each acquisition timer, then wait out its pass
  4. install        deploy/install-production.sh --ref <ref>
  5. reconcile      shrink the rows; a dry re-check must then find no change
  6. restart serve  mt-serve onto the new code
  7. verify         mt data health passes; mt data overview exits 0
  8. release timers started again, as found

The narration and a report go to project-documents/user/notes/; exit 0 only
when every check passed.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    ENV_FILE,
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
    verify_same_database,
    wait_for_serve,
)
from upgrade_production import hold_timers, release_timers  # noqa: E402

from manta_trading.config import Settings  # noqa: E402
from manta_trading.data.gaps.daily_gap_reconcile import (  # noqa: E402
    DailyReconcileReport,
    reconcile_daily_gaps,
)


def reconcile(url: str, *, apply: bool) -> DailyReconcileReport:
    with psycopg.connect(url, autocommit=True) as conn:
        report = reconcile_daily_gaps(conn, apply=apply)
    print(f"    {asdict(report)}")
    log_text(str(asdict(report)))
    return report


def settled(report: DailyReconcileReport) -> bool:
    """True when a dry check finds nothing left to change."""
    return report.deleted == report.shrunk == report.split == 0


def report_text(
    ref: str, before: DailyReconcileReport, after: DailyReconcileReport
) -> str:
    lines = [f"# #83 daily gap cutover, {ref}", "", "| Count | Value |", "|---|---|"]
    lines += [f"| {key} | {value} |" for key, value in asdict(before).items()]
    lines += ["", f"Re-check after the write: {asdict(after)}"]
    return "\n".join(lines) + "\n"


def verify_command(args: list[str]) -> None:
    result = run(["mt-run", *args], sudo=True, check=False)
    log_text(result.stdout or "")
    if result.returncode != 0:
        log_text(result.stderr or "")
        raise CutoverError(f"mt {' '.join(args)} exited {result.returncode}")
    print(f"    mt {' '.join(args)} passes")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if len(argv) == 2 else 2
    ref = argv[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log = start_log(NOTES_DIR / f"cutover-{ref}-{stamp}.log")
    print(f"narration -> {log}")
    url = Settings().timescale_db_url
    if url is None:
        print("MT_TIMESCALE_DB_URL is not set")
        return 2

    held: list[str] = []
    try:
        say(f"1/8 preflight — {ref}")
        commit = preflight(ref)
        dbname = verify_same_database(
            Path(".env").read_text(), out(["cat", str(ENV_FILE)], sudo=True),
            "MT_TIMESCALE_DB_URL",
        )  # fmt: skip
        print(f"    application URL targets the production database ({dbname})")

        say("2/8 dry check")
        planned = reconcile(url, apply=False)

        say("3/8 hold the acquisition timers")
        held = hold_timers()

        say(f"4/8 install {ref}")
        version = install(ref, commit)
        if ref.lstrip("v") not in version:
            raise CutoverError(f"installed mt reports {version!r}, expected {ref}")

        say("5/8 reconcile")
        reconcile(url, apply=True)
        after = reconcile(url, apply=False)
        if not settled(after):
            raise CutoverError(f"a re-check still finds changes: {asdict(after)}")
        report_path = NOTES_DIR / f"cutover-083-daily-gaps-{stamp}.md"
        report_path.write_text(report_text(ref, planned, after))
        print(f"    report -> {report_path}")

        say(f"6/8 restart {SERVE_UNIT}")
        restart_serve_if_active()
        if not unit_active(SERVE_UNIT):
            raise CutoverError(f"{SERVE_UNIT} is not active after the restart")
        wait_for_serve()

        say("7/8 verify")
        verify_command(["data", "health"])
        verify_command(["data", "overview"])
    except (CutoverError, subprocess.CalledProcessError, OSError, KeyError) as exc:
        say(f"FAILED: {exc!r}")
        return 1
    finally:
        say("8/8 release the timers")
        release_timers(held)

    say(f"PASS — production is at {ref} ({commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
