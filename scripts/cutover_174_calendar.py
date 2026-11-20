#!/usr/bin/env python3
"""Put the slice 174 calendar fix into production (issue #26).

    uv run python scripts/cutover_174_calendar.py <release-tag>

The release carries minute migration ``068_nyse_session_bounds``: explicit
NYSE/NASDAQ holidays for 2020-2028 and extended session bounds. This script
applies it itself (do not run ``mt data migrate apply`` first, though a host
where that already happened is handled), then reconciles gap rows over the
dates whose session status changed. Run as the operator from the dev checkout
root with the release checked out. Every step is check-then-act; after a
failure fix the cause and re-run.

Steps
  1. preflight      ref exists here and on origin; the checkout's application
                    URL is production's database
  2. dry check      while 068 is pending: the session change plan and the gap
                    rows over its dates; stop unless the plan is the pinned
                    EXPECTED_SESSION_CHANGES (nothing written yet)
  3. hold timers    stop each acquisition timer, then wait out its pass
  4. install        deploy/install-production.sh --ref <ref>
  5. migrate        minute to 068 (target verified); the re-plan must be empty
  6. reconcile      gap rows over the pinned opened/closed dates, each date's
                    own day (a re-run changes nothing)
  7. restart serve  mt-serve onto the new code
  8. verify         2021-12-31 is a NYSE session and 2025-01-09 is not;
                    mt data health passes; mt data overview exits 0
  9. release timers started again, as found (skipped after a failed migrate)

The narration and a report go to project-documents/user/notes/; exit 0 only
when every check passed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    ENV_FILE,
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
    verify_same_database,
    wait_for_serve,
)
from upgrade_production import hold_timers, release_timers  # noqa: E402

from manta_trading.config import Settings  # noqa: E402
from manta_trading.data.gaps.calendar_reconcile import (  # noqa: E402
    GRANULARITIES,
    ReconcileReport,
    day_window,
    gap_rows_over,
    reconcile_calendar_gaps,
)
from manta_trading.market.schema.nyse_session_changes import (  # noqa: E402
    EXPECTED_SESSION_CHANGES,
)
from manta_trading.market.schema.nyse_session_plan import (  # noqa: E402
    SessionChangePlan,
    plan_nyse_session_changes,
)

MIGRATION_ID = "068_nyse_session_bounds"
OPENED, CLOSED = date(2021, 12, 31), date(2025, 1, 9)


def is_applied(conn: psycopg.Connection[Any]) -> bool:
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE migration_id = %s)",
        (MIGRATION_ID,),
    ).fetchone()
    return bool(row and row[0])


def dry_check(url: str) -> SessionChangePlan | None:
    """The plan while 068 is pending (``None`` once applied); raises unless pinned."""
    with psycopg.connect(url, autocommit=True) as conn:
        if is_applied(conn):
            print(f"    {MIGRATION_ID} already applied")
            return None
        plan = plan_nyse_session_changes(conn)
        for calendar_id, changes in plan.items():
            print(
                f"    {calendar_id}: opened {sorted(map(str, changes.opened))}, "
                f"closed {len(changes.closed)}, retimed "
                f"{sorted(map(str, changes.retimed))}, appended {len(changes.appended)}"
            )
        for day in sorted(EXPECTED_SESSION_CHANGES.reconciled_dates()):
            counts = {
                g: sum(gap_rows_over(conn, g, day_window(day)).values())
                for g in GRANULARITIES
            }
            log_text(f"gap rows over {day}: {counts}")
    differing = {c: p for c, p in plan.items() if p != EXPECTED_SESSION_CHANGES}
    if differing:
        raise CutoverError(f"plan differs from EXPECTED_SESSION_CHANGES: {differing}")
    print("    plan equals EXPECTED_SESSION_CHANGES for NYSE and NASDAQ")
    return plan


def apply_and_reconcile(
    url: str, apply_migration: Callable[[], None]
) -> ReconcileReport:
    """Apply 068 when pending, require an empty re-plan, reconcile the gaps."""
    with psycopg.connect(url, autocommit=True) as conn:
        pending = not is_applied(conn)
    if pending:
        apply_migration()
    with psycopg.connect(url, autocommit=True) as conn:
        if not is_applied(conn):
            raise CutoverError(f"{MIGRATION_ID} is not applied")
        after = plan_nyse_session_changes(conn)
        if not all(changes.is_empty() for changes in after.values()):
            raise CutoverError(f"plan not empty after {MIGRATION_ID}: {after}")
        print("    re-plan is empty")
        return reconcile_calendar_gaps(
            conn, EXPECTED_SESSION_CHANGES.reconciled_dates()
        )


def report_text(
    ref: str, plan: SessionChangePlan | None, report: ReconcileReport
) -> str:
    lines = [f"# Slice 174 calendar cutover, {ref}", "", f"Migration `{MIGRATION_ID}`."]
    if plan is None:
        lines.append("Plan: migration already applied before this run.")
    else:
        lines.append("Plan (both calendars): equal to `EXPECTED_SESSION_CHANGES`.")
    lines += [
        "",
        "| Date | Granularity | Gap rows before | After | Skipped (never attempted) |",
    ]
    lines.append("|---|---|---|---|---|")
    for key in sorted(report.totals_before):
        lines.append(
            f"| {key[0]} | {key[1]} | {report.totals_before[key]} | "
            f"{report.totals_after[key]} | {report.skipped_never_attempted[key]} |"
        )
    lines += ["", f"Symbols changed: {len(report.changed)}"]
    lines += [
        f"- {c.day} {c.granularity} {c.symbol}: {c.before} -> {c.after}"
        for c in report.changed
    ]
    return "\n".join(lines) + "\n"


def verify_sessions() -> None:
    raw = out(
        [
            "mt-run", "data", "calendars", "sessions", "--calendar", "NYSE",
            "--from", "2021-12-30", "--to", "2025-01-10", "--json",
        ],
        sudo=True,
    )  # fmt: skip
    lines = raw.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("[")), None)
    if start is None:
        raise CutoverError(f"no JSON rows in {raw[-500:]}")
    dates = {r["session_date"] for r in json.loads("\n".join(lines[start:]))}
    if OPENED.isoformat() not in dates or CLOSED.isoformat() in dates:
        raise CutoverError(f"NYSE sessions: {OPENED} in={OPENED.isoformat() in dates}, "
                           f"{CLOSED} in={CLOSED.isoformat() in dates}")  # fmt: skip
    print(f"    NYSE: {OPENED} is a session, {CLOSED} is not")


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
    release = True
    try:
        say(f"1/9 preflight — {ref}")
        commit = preflight(ref)
        dbname = verify_same_database(
            Path(".env").read_text(), out(["cat", str(ENV_FILE)], sudo=True),
            "MT_TIMESCALE_DB_URL",
        )  # fmt: skip
        print(f"    application URL targets the production database ({dbname})")

        say("2/9 dry check")
        plan = dry_check(url)

        say("3/9 hold the acquisition timers")
        held = hold_timers()

        say(f"4/9 install {ref}")
        version = install(ref, commit)
        if ref.lstrip("v") not in version:
            raise CutoverError(f"installed mt reports {version!r}, expected {ref}")

        say("5/9 migrate, 6/9 reconcile")
        release = False  # a half-migrated host keeps its timers stopped
        report = apply_and_reconcile(url, lambda: migrate("minute", MIGRATION_ID))
        release = True
        text = report_text(ref, plan, report)
        log_text(text)
        report_path = NOTES_DIR / f"cutover-174-calendar-{stamp}.md"
        report_path.write_text(text)
        print(f"    {len(report.changed)} symbol-days changed; report -> {report_path}")

        say(f"7/9 restart {SERVE_UNIT}")
        restart_serve_if_active()
        if not unit_active(SERVE_UNIT):
            raise CutoverError(f"{SERVE_UNIT} is not active after the restart")
        wait_for_serve()

        say("8/9 verify")
        verify_sessions()
        verify_command(["data", "health"])
        verify_command(["data", "overview"])
    except (CutoverError, subprocess.CalledProcessError, OSError, KeyError) as exc:
        say(f"FAILED: {exc!r}")
        if not release:
            say(f"timers left stopped after a failed migrate: {held}")
        return 1
    finally:
        if release:
            say("9/9 release the timers")
            release_timers(held)

    say(f"PASS — production is at {ref} ({commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
