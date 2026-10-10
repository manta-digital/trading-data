#!/usr/bin/env python
"""Quarterly tick restore drill (slice 228): archive, database, fallback.

Proves in one run that (1) the tick archive restores from restic, (2)
``trading_tick`` restores from base + WAL and matches production, and (3) a
database rebuilt from the *restored* archive matches the restored one.
Everything it creates lives in ``/data/restore-test/228-drill-<stamp>/`` and
is removed at the end, pass or fail. Production is only read; the one write
is a WAL segment switch.

Run as manta from the checkout root. It prompts once for sudo (restic,
chown, rm), writes ``project-documents/user/notes/<date>-228-tick-restore-drill.md``
and exits 0 only when every check passed:

    uv run python scripts/drill_tick_restore.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import psycopg  # noqa: E402
from backup_clusters import TABLE_PATH, cluster  # noqa: E402
from cutover_227_helpers import TICK, Step, StepFailed, StepStatus  # noqa: E402
from cutover_227_host import env_value  # noqa: E402
from cutover_common import say  # noqa: E402
from drill_228_context import DrillContext  # noqa: E402
from drill_228_host import run, sudo_n  # noqa: E402
from drill_228_lifecycle import (  # noqa: E402
    REPORT_DIR,
    DrillError,
    remove_drill_dir,
    stop_scratch_server,
)
from drill_228_rebuild import step_compare_rebuild, step_rebuild  # noqa: E402
from drill_228_steps import (  # noqa: E402
    step_compare_production,
    step_hold,
    step_prepare,
    step_restore_archive,
    step_restore_database,
    step_switch,
)

from manta_trading.data.tick.constants import TICK_ARCHIVE_DIR_ENV  # noqa: E402

Action = Callable[[DrillContext, Step], None]
StepSpec = tuple[str, str, Action]

STEPS: list[StepSpec] = [
    (
        "prepare",
        "drill lock held; sudo primed; leftovers cleared; free space >= 3x footprint",
        step_prepare,
    ),
    (
        "hold production",
        "both tick locks taken; SELECT on every public table; settings read; "
        "live archive equals the latest snapshot",
        step_hold,
    ),
    ("restore point", "the switched segment is archived within the bound", step_switch),
    (
        "restore archive",
        "restored files and bytes equal the live archive's backed-up set",
        step_restore_archive,
    ),
    (
        "restore database",
        "base verified; auto.conf emptied; recovery reached step 2's segment",
        step_restore_database,
    ),
    (
        "compare with production",
        "row counts, tick_trade fingerprint, bookkeeping md5 equal; locks held",
        step_compare_production,
    ),
    (
        "rebuild from the restored archive",
        "provision, migrate, adopt per job, pass --estimate-only, ingest exit 0",
        step_rebuild,
    ),
    (
        "compare rebuild with restore",
        "tick_trade fingerprint equal; bookkeeping differences only in TD4's set",
        step_compare_rebuild,
    ),
]
CLEANUP_NUMBER = 8
REPORT_NAME = "228-tick-restore-drill.md"
PASS_LINE = "PASS: archive, database, fallback"
#: Failures a step can end in; each is recorded and stops the run.
STEP_ERRORS = (
    StepFailed,
    DrillError,
    psycopg.Error,
    subprocess.SubprocessError,
    OSError,
    ValueError,
    LookupError,
)


def _run_one(ctx: DrillContext, step: Step, action: Action) -> bool:
    say(f"Step {step.number}: {step.title}")
    try:
        action(ctx, step)
    except STEP_ERRORS as exc:
        # Process boundary: the failure is recorded and the run stops here;
        # cleanup and the report still run.
        step.status = StepStatus.FAIL
        step.seen.append(f"FAILED: {type(exc).__name__}: {exc}")
        say(f"  FAIL: {exc}")
        return False
    step.status = StepStatus.PASS
    say("  PASS")
    return True


def cleanup(ctx: DrillContext, step: Step) -> None:
    """Step 8: close production, stop the scratch server, remove the drill dir."""
    try:
        if ctx.prod is not None:
            ctx.prod.close()  # session advisory locks go with the connection
            ctx.prod = None
        if ctx.drill is not None:
            stopped = stop_scratch_server(ctx.drill, run)
            step.seen.append(f"scratch server stopped: {stopped}")
            remove_drill_dir(ctx.drill, sudo_n)
            step.seen.append(f"removed {ctx.drill}")
    finally:
        if ctx.lock is not None:
            ctx.lock.release()


def run_steps(
    ctx: DrillContext, specs: Sequence[StepSpec] = STEPS, through: int | None = None
) -> list[Step]:
    """Run steps in order (``through``: last step number to run), stop at the
    first failure, and always clean up."""
    chosen = list(specs) if through is None else list(specs)[: through + 1]
    steps = [Step(n, title, expected) for n, (title, expected, _) in enumerate(chosen)]
    clean = Step(CLEANUP_NUMBER, "clean up", "no drill directory, no scratch server")
    try:
        for step, (_, _, action) in zip(steps, chosen, strict=True):
            if not _run_one(ctx, step, action):
                break
    finally:
        _run_one(ctx, clean, cleanup)
    return [*steps, clean]


def render_report(
    ctx: DrillContext, steps: list[Step], started: str, full: bool
) -> str:
    passed = all(s.status is StepStatus.PASS for s in steps)
    if passed and full:
        outcome = PASS_LINE
    elif passed:
        outcome = f"PASS through step {steps[-2].number} only (partial run)"
    else:
        outcome = "FAIL"
    day = started[:10].replace("-", "")
    out = [
        "---",
        "docType: notes",
        "slice: tick-restore-drill",
        "project: trading-data",
    ]
    out += [f"dateCreated: {day}", f"dateUpdated: {day}", "---", ""]
    out += ["# Tick restore drill (slice 228)", "", f"Started {started}.", ""]
    for step in steps:
        out += [f"## Step {step.number}: {step.title} — {step.status}", ""]
        out += [
            f"Expected: {step.expected}",
            "",
            "Seen:",
            "",
            "```",
            *step.seen,
            "```",
            "",
        ]
    out += ["## Timings", ""]
    out += [f"- {name}: {seconds:.1f}" for name, seconds in ctx.timings.items()] or [
        "- none"
    ]
    out += ["", "## Notes", "", *[f"- {n}" for n in ctx.notes or ["none"]], ""]
    for title, lines in ctx.evidence.items():
        out += [f"## Evidence: {title}", "", "```", *lines, "```", ""]
    return "\n".join([*out, outcome, ""])


def execute(
    ctx: DrillContext,
    report: Path,
    specs: Sequence[StepSpec] = STEPS,
    through: int | None = None,
) -> int:
    """Run the drill, write the report, return the exit status."""
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    steps = run_steps(ctx, specs, through)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(ctx, steps, started, through is None))
    passed = all(s.status is StepStatus.PASS for s in steps)
    say(f"Report: {report} — {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def make_context(checkout: Path) -> DrillContext:
    archive = env_value(checkout / ".env", TICK_ARCHIVE_DIR_ENV)
    if not archive or not Path(archive).is_dir():
        sys.exit(f"{TICK_ARCHIVE_DIR_ENV} is not a directory in .env: {archive!r}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DrillContext(checkout, cluster(TABLE_PATH, TICK), Path(archive), stamp)


def report_path(checkout: Path) -> Path:
    return (
        checkout / REPORT_DIR / f"{datetime.now(UTC).date().isoformat()}-{REPORT_NAME}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.parse_args(argv)
    checkout = Path.cwd()
    if not (checkout / "scripts" / Path(__file__).name).exists():
        sys.exit("run from the checkout root")
    return execute(make_context(checkout), report_path(checkout))


if __name__ == "__main__":
    sys.exit(main())
