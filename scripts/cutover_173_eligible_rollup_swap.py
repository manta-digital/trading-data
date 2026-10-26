#!/usr/bin/env python
"""Release slice 173 on manta9000 and swap the eligible rollups in, in one command.

    uv run python scripts/cutover_173_eligible_rollup_swap.py <ref>

Run as ``manta`` from the dev checkout root, with the checkout at ``<ref>``.
Each step is check-then-act, so a rerun after a kill at any step finishes
the swap:

1. **precheck** — the ref exists and is on origin, then the swap state is
   read from the catalog. Before the swap, every ``_next`` object must exist
   and ``caggs verify --target next`` and ``rebuild-coverage --verify --target
   next`` must pass. Once the swap has committed there is no ``_next`` left to
   check, so a rerun skips this. Fails: nothing changed, timers never held.

The acquisition timers are held from here on (running passes are waited out):

2. **install** — ``install-production.sh --ref <ref>``; skipped when
   ``/opt/manta-trading`` is already at the ref.
3. **swap** — refuses unless the checkout's maintenance URL is the production
   database, then applies migration 064 unless it is applied: the renames and
   ``data_status`` in one transaction, then the ``_prev`` drops.
4. **verify** — ``caggs verify`` on all four rollups; no ``_next`` or
   ``_prev`` object remains; production's ``mt data health`` passes the
   rollups' and coverage's freshness and the value-parity check.
5. **serve** — restarts mt-serve onto the new code.

On success the timers are released. On a failure they are released only if
the catalog still shows the swap not begun (a refused install, a rolled-back
rename transaction); any other failure, or a state that cannot be read, leaves
them **held**: ``mt data health`` and the operator overview show it, and the
fix is to rerun. The report (``project-documents/user/notes/<date>-173-
cutover.md``) lists each step's outcome, rows per minute cagg and the verify
output. Exit 0 only when every step passed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cutover_172_build as build  # noqa: E402
import cutover_172_eligible_rollups as prior  # noqa: E402
import cutover_173_checks as checks  # noqa: E402
import psycopg  # noqa: E402
from cutover_172_eligible_rollups import (  # noqa: E402
    Context,
    Step,
    StepFailed,
    _connect,
    _mt,
    hold_timers,
    release_timers,
    run_steps,
)
from cutover_common import (  # noqa: E402
    NOTES_DIR,
    migrate,
    preflight,
    restart_serve_if_active,
    run,
    say,
    start_log,
)

MIGRATION_TRACK = "minute"
MIGRATION_ID = "064_minute_eligible_rollup_swap"
_PARITY_FAILURE = 2  # caggs verify's exit code when a view is out of parity


def _state() -> checks.SwapState:
    with _connect() as conn:
        return checks.read_swap_state(conn)


def step_precheck(ctx: Context, step: Step) -> None:
    ctx.commit = preflight(ctx.ref)
    step.note(f"{ctx.ref} = {ctx.commit[:12]}, on origin")
    state = _state()
    if state is not checks.SwapState.BEFORE:
        step.note(f"swap state {state}: _next already swapped in — not rechecked")
        return
    prior.step_verify(ctx, step)
    code, _ = _mt(
        "rebuild-coverage",
        "--family",
        "minute",
        "--target",
        "next",
        "--verify",
        as_owner=True,
    )
    if code != 0:
        raise StepFailed(f"{checks.SWAP_NAMES[-1].next} --verify exited {code}")
    step.note(f"{checks.SWAP_NAMES[-1].next} verified")


def step_swap(ctx: Context, step: Step) -> None:
    migrate(MIGRATION_TRACK, MIGRATION_ID)
    step.note(f"{MIGRATION_ID} applied; minute track has nothing pending")


def _verify_rollups(ctx: Context, step: Step) -> None:
    code, output = _mt("verify", "--granularity", "all", "--json")
    ctx.verify_output = output
    if code == _PARITY_FAILURE:
        failing = [r["view"] for r in build.json_list(output) if not r["in_parity"]]
        raise StepFailed(f"out of parity: {', '.join(failing)}")
    if code != 0:
        raise StepFailed(f"caggs verify exited {code}")
    step.note("every minute rollup at parity")


def step_verify(ctx: Context, step: Step) -> None:
    _verify_rollups(ctx, step)
    state = _state()
    if state is not checks.SwapState.DONE:
        raise StepFailed(f"swap state {state}: _prev objects remain")
    step.note("no _next or _prev object remains")
    # Production's front door, so the verdict uses production's settings
    # (the dev .env differs) and the installed code's value-parity check.
    health = run(["mt-run", "data", "health", "--json"], sudo=True, check=False)
    failing = checks.failing_health(health.stdout)
    if failing:
        raise StepFailed("mt data health: " + "; ".join(failing))
    step.note(f"mt data health passes {', '.join(checks.REQUIRED_HEALTH)}")


def step_serve(ctx: Context, step: Step) -> None:
    # Always, not only when this run installed: a rerun after a kill must
    # still move mt-serve off the code that reports the old default.
    restart_serve_if_active()
    step.note("mt-serve restarted if it was active")


PRECHECK_STEPS: list[tuple[str, Callable[[Context, Step], None]]] = [
    ("precheck", step_precheck),
]
SWAP_STEPS: list[tuple[str, Callable[[Context, Step], None]]] = [
    ("install", prior.step_install),
    ("swap", step_swap),
    ("verify", step_verify),
    ("serve", step_serve),
]


def swap_untouched() -> bool:
    """True only when the catalog shows 064 not begun. Unreadable is False:
    the timers then stay held, the safe side."""
    try:
        return _state() is checks.SwapState.BEFORE
    except (StepFailed, checks.CheckFailed, psycopg.Error, OSError) as exc:
        say(f"could not read the swap state ({exc}) — keeping the timers held")
        return False


def _report(started: str, steps: list[Step], ctx: Context, ok: bool) -> str:
    rows = [(step.title, step.outcome, step.seen) for step in steps]
    try:
        with _connect() as conn:
            counts = checks.row_counts(conn)
    except (StepFailed, checks.CheckFailed, psycopg.Error, OSError) as exc:
        return checks.render_report(started, ctx.ref, ok, rows, None, str(exc))
    return checks.render_report(started, ctx.ref, ok, rows, counts, ctx.verify_output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ref", help="tag or commit to install (on origin)")
    args = parser.parse_args(argv)
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    start_log(NOTES_DIR / f"{started[:10]}-173-cutover.log")
    ctx = Context(args.ref)
    steps: list[Step] = []
    held: list[str] = []
    ok = False  # stays False unless every step returns PASS
    try:
        if run_steps(ctx, PRECHECK_STEPS, steps):
            try:
                hold_timers(held)
                ok = run_steps(ctx, SWAP_STEPS, steps)
            finally:
                if ok or swap_untouched():
                    release_timers(held)
                else:
                    say(f"timers stay HELD: {', '.join(held)} — rerun to finish")
    finally:
        # Written on every exit, an unexpected exception included: the
        # traceback still propagates, and the report says how far it got.
        report = checks.report_path(NOTES_DIR, started)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(_report(started, steps, ctx, ok))
        say(f"report written to {report} — {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
