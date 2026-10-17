#!/usr/bin/env python
"""Release slice 172 on manta9000 and build the eligible rollups, in one command.

    uv run python scripts/cutover_172_eligible_rollups.py <ref>

Run as ``manta`` from the dev checkout root, with the checkout at ``<ref>``.
Steps, each check-then-act, so a rerun skips what is done and resumes at the
first step that is not:

1. **preflight** — the ref exists and is on origin. Fails: nothing changed.
2. **install** — ``install-production.sh --ref <ref>``; skipped when
   ``/opt/manta-trading`` is already at the ref. Fails: timers still held
   until the script exits; rerun after fixing the cause.
3. **migrate** — refuses unless the checkout's maintenance URL is the
   production database (``verify_migrate_target``), then applies 063 unless
   it is applied. Fails as install.
4. **serve** — restarts mt-serve onto the new ref, when this run installed it.

The acquisition timers are held for steps 1-4 (running passes are waited
out) and released before the build, which runs beside the passes:

5. **build** — refuses while any minute chunk holds an unevaluated off-tape
   mark (the history sweep must finish first), or when the database volume's
   free space cannot be measured (``pg_lsclusters`` gives the local cluster's
   data directory for the URL's port; the migration role cannot read
   ``data_directory``). Resumes any ``_next`` job a killed run left
   paused. Then for 4h, 1h, 15m, 5m: pause the view's jobs, ``caggs repair
   --target next --order newest-first`` (which skips every window already at
   parity), resume them (also when repair fails). Fails: non-zero exit;
   rerun, and parity skips what is built.
6. **coverage** — pause ``minute_coverage_next``'s jobs, ``rebuild-coverage
   --family minute --target next``, resume, then its ``--verify``. Fails as 5.
7. **verify** — ``caggs verify --target next --granularity all``. Fails:
   names the views out of parity.

The report (``project-documents/user/notes/<date>-172-cutover.md``) lists
each step's outcome, rows per ``_next`` view, the verify output, and each
``_next`` refresh job's last run duration against its schedule. Exit 0 only
when every step passed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cutover_172_build as build  # noqa: E402
import psycopg  # noqa: E402
from cutover_common import (  # noqa: E402
    INSTALL_DIR,
    NOTES_DIR,
    SERVICE_USER,
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
    wait_for_unit_to_end,
)
from psycopg.rows import dict_row  # noqa: E402

MIGRATION_TRACK = "minute"
MIGRATION_ID = "063_minute_eligible_rollups_next"
HELD_TIMERS = ("mt-minute-pass.timer", "mt-daily-pass.timer", "mt-kalshi-pass.timer")
HELD_PASSES = tuple(t.replace(".timer", ".service") for t in HELD_TIMERS)
_PARITY_FAILURE = 2  # caggs verify's exit code when a view is out of parity


@dataclass
class Context:
    ref: str
    commit: str = ""
    installed: bool = False
    verify_output: str = ""


@dataclass
class Step:
    title: str
    outcome: str = "not run"
    seen: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.seen.append(text)
        say(f"  {text}")


class StepFailed(RuntimeError):
    pass


# --- host seams --------------------------------------------------------------


def _mt(*args: str) -> tuple[int, str]:
    """``uv run mt data caggs <args>`` from this checkout, streamed and logged."""
    cmd = ["uv", "run", "mt", "data", "caggs", *args]
    say(f"  $ {' '.join(cmd)}")
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as proc:
        assert proc.stdout is not None
        lines = []
        for line in proc.stdout:
            print(line, end="", flush=True)
            log_text(line)
            lines.append(line)
    return proc.returncode, "".join(lines)


def _maintenance_url() -> str:
    from manta_trading.config import Settings

    url = Settings().timescale_maintenance_url
    if not url:
        raise StepFailed("MT_TIMESCALE_MAINTENANCE_URL is not set in .env")
    return str(url)


def _connect() -> psycopg.Connection[Any]:
    return psycopg.connect(_maintenance_url(), autocommit=True, row_factory=dict_row)


def _installed_commit() -> str:
    return out(
        ["-u", SERVICE_USER, "git", "-C", str(INSTALL_DIR), "rev-parse", "HEAD"],
        sudo=True,
    )


# --- steps -------------------------------------------------------------------


def step_preflight(ctx: Context, step: Step) -> None:
    ctx.commit = preflight(ctx.ref)
    step.note(f"{ctx.ref} = {ctx.commit[:12]}, on origin")


def step_install(ctx: Context, step: Step) -> None:
    if _installed_commit() == ctx.commit:
        step.note(f"{INSTALL_DIR} already at {ctx.commit[:12]} — skipped")
        return
    step.note(install(ctx.ref, ctx.commit))
    ctx.installed = True


def step_migrate(ctx: Context, step: Step) -> None:
    migrate(MIGRATION_TRACK, MIGRATION_ID)
    step.note(f"{MIGRATION_ID} applied; minute track has nothing pending")


def step_serve(ctx: Context, step: Step) -> None:
    if not ctx.installed:
        step.note("nothing installed this run — mt-serve left alone")
        return
    restart_serve_if_active()
    step.note("mt-serve restarted if it was active")


def _repair_one(
    conn: psycopg.Connection[Any], step: Step, gran: Any, view: str, headroom: float
) -> None:
    # No verify first: repair parity-checks each window and skips the built
    # ones, and a whole-table verify per rollup is four extra raw scans.
    view_jobs = build.jobs(conn, view)
    step.note(f"{view}: paused job(s) {build.set_scheduled(conn, view_jobs, False)}")
    try:
        code, _ = _mt(
            "repair",
            "--target",
            "next",
            "--order",
            "newest-first",
            "--granularity",
            gran.value,
            "--assume-headroom-gb",
            str(headroom),
        )
    finally:
        step.note(
            f"{view}: resumed job(s) {build.set_scheduled(conn, view_jobs, True)}"
        )
    if code != 0:
        raise StepFailed(f"repair of {view} exited {code}; rerun to resume")
    step.note(f"{view}: repaired")


def step_build(ctx: Context, step: Step) -> None:
    with _connect() as conn:
        leftovers = build.paused_next_jobs(conn)
        if leftovers:
            ids = build.set_scheduled(conn, leftovers, True)
            step.note(f"resumed job(s) {ids} left paused by an earlier run")
        build.require_sweep_complete(conn)
        step.note("no minute chunk holds an unevaluated off-tape mark")
        headroom = build.headroom_gb(_maintenance_url())
        step.note(f"free space on the database volume: {headroom} GB")
        for gran, view in build.BUILD_ORDER:
            _repair_one(conn, step, gran, view, headroom)


def step_coverage(ctx: Context, step: Step) -> None:
    family = ("--family", "minute", "--target", "next")
    with _connect() as conn:
        view_jobs = build.jobs(conn, build.NEXT_COVERAGE_VIEW)
        ids = build.set_scheduled(conn, view_jobs, False)
        step.note(f"{build.NEXT_COVERAGE_VIEW}: paused job(s) {ids}")
        try:
            code, _ = _mt("rebuild-coverage", *family)
        finally:
            ids = build.set_scheduled(conn, view_jobs, True)
            step.note(f"{build.NEXT_COVERAGE_VIEW}: resumed job(s) {ids}")
    if code != 0:
        raise StepFailed(f"rebuild-coverage exited {code}; rerun to resume")
    code, _ = _mt("rebuild-coverage", *family, "--verify")
    if code != 0:
        raise StepFailed(f"{build.NEXT_COVERAGE_VIEW} --verify exited {code}")
    step.note(f"{build.NEXT_COVERAGE_VIEW}: rebuilt and verified")


def step_verify(ctx: Context, step: Step) -> None:
    code, output = _mt("verify", "--target", "next", "--granularity", "all", "--json")
    ctx.verify_output = output
    if code == 0:
        step.note("every _next rollup at parity")
        return
    if code != _PARITY_FAILURE:
        raise StepFailed(f"caggs verify exited {code}")
    reports = build.json_list(output)
    failing = [r["view"] for r in reports if not r["in_parity"]]
    raise StepFailed(f"out of parity: {', '.join(failing)}")


RELEASE_STEPS: list[tuple[str, Callable[[Context, Step], None]]] = [
    ("preflight", step_preflight),
    ("install", step_install),
    ("migrate", step_migrate),
    ("serve", step_serve),
]
BUILD_STEPS: list[tuple[str, Callable[[Context, Step], None]]] = [
    ("build", step_build),
    ("coverage", step_coverage),
    ("verify", step_verify),
]


def run_steps(
    ctx: Context,
    steps: list[tuple[str, Callable[[Context, Step], None]]],
    record: list[Step],
) -> bool:
    for title, action in steps:
        step = Step(title)
        record.append(step)
        say(f"step: {title}")
        try:
            action(ctx, step)
        except (
            StepFailed,
            CutoverError,
            build.BuildRefused,
            subprocess.SubprocessError,
            psycopg.Error,
            OSError,
        ) as exc:
            # Process boundary: the failure is recorded, the run stops here,
            # and main() still writes the report.
            step.outcome = "FAIL"
            step.note(f"FAIL: {exc}")
            return False
        step.outcome = "PASS"
    return True


def hold_timers() -> list[str]:
    held = [t for t in HELD_TIMERS if unit_active(t)]
    for timer in held:
        run(["systemctl", "stop", timer], sudo=True)
    for unit in HELD_PASSES:
        wait_for_unit_to_end(unit)
    say(f"held timers: {', '.join(held) or 'none were active'}")
    return held


def release_timers(held: list[str]) -> None:
    for timer in held:
        run(["systemctl", "start", timer], sudo=True)
    say(f"released timers: {', '.join(held) or 'none'}")


def _report(started: str, steps: list[Step], ctx: Context, ok: bool) -> str:
    rows = [(step.title, step.outcome, step.seen) for step in steps]
    try:
        with _connect() as conn:
            figures = (build.row_counts(conn), build.refresh_load(conn))
    except (StepFailed, psycopg.Error) as exc:
        return build.render_report(started, ctx.ref, ok, rows, None, str(exc))
    return build.render_report(started, ctx.ref, ok, rows, figures, ctx.verify_output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ref", help="tag or commit to install (on origin)")
    args = parser.parse_args(argv)
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    start_log(NOTES_DIR / f"{started[:10]}-172-cutover.log")
    ctx = Context(args.ref)
    steps: list[Step] = []
    held = hold_timers()
    try:
        ok = run_steps(ctx, RELEASE_STEPS, steps)
    finally:
        release_timers(held)
    ok = ok and run_steps(ctx, BUILD_STEPS, steps)
    report = build.report_path(NOTES_DIR, started)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_report(started, steps, ctx, ok))
    say(f"report written to {report} — {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
