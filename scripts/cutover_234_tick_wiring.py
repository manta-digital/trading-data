#!/usr/bin/env python
"""Put the tick pass into production on manta9000, in one command.

Slice 234, TD4. Nine steps, each recording what it expected against what it
saw; the first failure stops the run and the report is still written to
``project-documents/user/notes/<date>-234-cutover.md``. Recovery is always:
fix the cause, re-run the whole script. Every step is check-then-act, so a
re-run reports what is already done and changes nothing there.

The acquisition timers are held for the whole run (a running pass is waited
out first) and released in ``finally``. Runs as ``manta`` from the checkout
root; root steps go through the narrow sudo list (``mt-run``, the installer,
the env-file read, the named timers). Nothing is bought: step 7 refuses to
continue if the universe wants anything.

    uv run python scripts/cutover_234_tick_wiring.py <ref> [--note TEXT ...]

Each ``--note`` is a preamble line of the report: the Project Manager's
designation for migration 060 on production, known test failures (Task 10).
"""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cutover_234_host as host  # noqa: E402
from cutover_227_helpers import Step, StepFailed, StepStatus  # noqa: E402
from cutover_227_host import require  # noqa: E402
from cutover_234_checks import (  # noqa: E402
    ADD_TICK_ENV,
    PRIMARY_API_EXTRA,
    PRIMARY_PASS_CONNECTIONS,
    TICK_API_CONNECTIONS,
    TICK_PASS_CONNECTIONS,
    add_tick_env_command,
    budget_line,
    fits,
    missing_keys,
    purchase_count,
    purchase_wants,
    render_report,
    serve_has_workers,
    tick_env_keys,
)
from cutover_common import (  # noqa: E402
    ENV_FILE,
    INSTALL_DIR,
    NOTES_DIR,
    SERVE_UNIT,
    env_value,
    fire_unit,
    install,
    migrate,
    out,
    preflight,
    read_unit_journal,
    restart_serve_if_active,
    run,
    say,
    unit_active,
    unit_result,
    wait_for_unit_to_end,
)

TICK_UNIT = "mt-tick-pass.service"
MIGRATION_TRACK = "minute"
MIGRATION_ID = "060_pass_kind_tick"
#: The acquisition-slice passes that a timer fires (234 holds them all).
HELD_TIMERS = ("mt-minute-pass.timer", "mt-daily-pass.timer", "mt-kalshi-pass.timer")
HELD_PASSES = tuple(t.replace(".timer", ".service") for t in HELD_TIMERS)
MEMORY_PEAK = "memory peak"


@dataclass
class Context:
    ref: str
    checkout: Path
    commit: str = ""
    production_env: str = ""
    preamble: list[str] = field(default_factory=list)


# --- Steps 1–5: preconditions, archive, migrate, install, serve -----------------


def step_preconditions(ctx: Context, step: Step) -> None:
    owner = ctx.checkout.owner()
    require(getpass.getuser() == owner, step, f"run as {owner}, the checkout owner")
    ctx.commit = preflight(ctx.ref)
    step.seen.append(f"ref {ctx.ref} = {ctx.commit[:12]}, on origin")
    ctx.production_env = out(["cat", str(ENV_FILE)], sudo=True)
    keys = tick_env_keys((ctx.checkout / ADD_TICK_ENV).read_text())
    missing = missing_keys(ctx.production_env, keys)
    step.seen.append(
        f"tick keys present by name: {len(keys) - len(missing)}/{len(keys)}"
    )
    if missing:
        step.seen.append(f"missing: {', '.join(missing)}")
        step.seen.append(f"PM host act: {add_tick_env_command(ctx.checkout)}")
    require(not missing, step, f"{ENV_FILE} lacks the tick keys; run add-tick-env.sh")


def step_archive(ctx: Context, step: Step) -> None:
    root = Path(env_value(ctx.production_env, "MT_TICK_ARCHIVE_DIR"))
    problems = host.archive_problems(root)
    step.seen.append(f"{root}: {len(problems)} entries not shared with {host.GROUP}")
    if problems:
        host.share_archive(root)
        problems = host.archive_problems(root)
        step.seen.append(f"after chgrp/chmod: {len(problems)} remain")
    require(not problems, step, f"not group {host.GROUP}-writable: {problems[:5]}")


def step_migrate(ctx: Context, step: Step) -> None:
    migrate(MIGRATION_TRACK, MIGRATION_ID)
    url = env_value((ctx.checkout / ".env").read_text(), "MT_TIMESCALE_MAINTENANCE_URL")
    definition = host.pass_check_definition(url)
    step.seen.append(f"pass_runs_pass_check: {definition}")
    require("'tick'" in definition, step, "pass_runs_pass_check does not admit tick")


def step_install(ctx: Context, step: Step) -> None:
    version = install(ctx.ref, ctx.commit)
    step.seen.append(f"{INSTALL_DIR} at {ctx.commit[:12]}; {version}")


def step_serve(ctx: Context, step: Step) -> None:
    if not unit_active(SERVE_UNIT):
        step.seen.append(f"{SERVE_UNIT} not active: nothing to restart or probe")
        return
    restart_serve_if_active()
    health = host.api_health()
    step.seen.append(f"/api/v1/health tick_db={health.get('tick_db')}")
    require(health.get("tick_db") == "ok", step, "the API's tick pool is not ok")


# --- Steps 6–9: connections, purchase guard, firing, surfaces --------------------


def step_connections(ctx: Context, step: Step) -> None:
    unit = (host.SYSTEMD_DIR / SERVE_UNIT).read_text()
    require(not serve_has_workers(unit), step, f"{SERVE_UNIT} sets --workers")
    step.seen.append(f"{SERVE_UNIT}: one worker (no --workers)")
    clusters = (
        ("tick", "MT_TICK_DB_URL", TICK_API_CONNECTIONS + TICK_PASS_CONNECTIONS),
        (
            "primary",
            "MT_TIMESCALE_DB_URL",
            PRIMARY_API_EXTRA + PRIMARY_PASS_CONNECTIONS,
        ),
    )
    for name, key, needed in clusters:
        load = host.cluster_load(env_value(ctx.production_env, key))
        step.seen.append(budget_line(name, load, needed))
        require(fits(load, needed), step, f"the {name} cluster lacks {needed} slots")


def step_purchase_guard(ctx: Context, step: Step) -> None:
    result = run(
        ["mt-run", "data", "tick", "pass", "--estimate-only", "--json"],
        sudo=True,
        check=False,
    )
    step.seen.append(f"estimate-only exit {result.returncode}")
    wants = purchase_wants(json.loads(result.stdout))
    step.seen += wants or ["nothing wanted; planned cost 0"]
    require(not wants, step, "the universe wants a purchase; a cutover never buys")


def step_fire(ctx: Context, step: Step) -> None:
    declared = host.installed_memory_max()
    sampler = host.CgroupSampler(TICK_UNIT)
    sampler.start()
    try:
        cursor, started = fire_unit(TICK_UNIT, ["tick"])
    finally:
        sampler.stop()
    result, status = unit_result(TICK_UNIT)
    journal = read_unit_journal(cursor, TICK_UNIT)
    messages = [m for _, m in journal.entries]
    peaks = journal.find(MEMORY_PEAK)
    last = _tick_last_run()
    step.seen += [
        f"fired {started}; result={result} exit={status}",
        f"memory.max {sampler.memory_max} vs declared {declared}",
        f"oom_kill {sampler.oom_kill}",
        f"newest TICK row: {last}",
        f"journal: {peaks[-1][1] if peaks else 'no memory peak line'}",
        f"purchases submitted: {purchase_count(messages)}",
    ]
    require(result == "success" and status == "0", step, "the tick unit failed")
    require(sampler.memory_max == str(declared), step, "memory.max ≠ MemoryMax")
    require(sampler.oom_kill == 0, step, "the slice saw an OOM kill")
    require(bool(peaks), step, "no memory peak line in the journal")
    require(last.get("outcome") == "COMPLETE", step, "newest TICK row not COMPLETE")


def _tick_last_run() -> dict[str, object]:
    payload = json.loads(out(["mt-run", "data", "overview", "--json"], sudo=True))
    line = next(p for p in payload["passes"] if p["pass"] == "tick")
    return dict(line["last_run"] or {})


def step_surfaces(ctx: Context, step: Step) -> None:
    checks = (
        (["mt-run", "status"], ("== tick:",)),
        (["mt-run", "data", "overview"], ("manual (no timer)", "tick manual")),
        (["mt-run", "data", "health"], ("OK   tick database", "OK   tick retention")),
    )
    for args, needles in checks:
        result = run(args, sudo=True, check=False)
        lines = [x for x in result.stdout.splitlines() if "tick" in x]
        step.seen += [f"$ sudo {' '.join(args)} (exit {result.returncode})", *lines]
        for needle in needles:
            require(needle in result.stdout, step, f"{args[-1]}: no {needle!r}")


STEPS: list[tuple[str, str, Callable[[Context, Step], None]]] = [
    (
        "preconditions",
        "checkout owner; ref on origin; five tick keys",
        step_preconditions,
    ),
    ("archive", "every archive entry group manta-trading, g+w, dirs g+s", step_archive),
    ("migrate", f"{MIGRATION_ID} applied; the CHECK admits tick", step_migrate),
    ("install", "/opt at the ref; mt --version", step_install),
    ("serve", "mt-serve restarted; /api/v1/health tick_db ok", step_serve),
    ("connections", "one worker; both clusters within budget", step_connections),
    ("purchase guard", "estimate-only wants nothing, cost 0", step_purchase_guard),
    ("fire", "exit 0; memory.max = MemoryMax; oom_kill 0; COMPLETE row", step_fire),
    ("surfaces", "status, overview and health show tick", step_surfaces),
]


def run_steps(ctx: Context, steps: list[Step]) -> bool:
    for step, (_name, _expected, action) in zip(steps, STEPS, strict=True):
        say(f"Step {step.number}: {step.title}")
        try:
            action(ctx, step)
        except (
            StepFailed,
            subprocess.SubprocessError,
            OSError,
            ValueError,
            KeyError,
            StopIteration,
        ) as exc:
            # Process boundary: any failure of a step is recorded and the run
            # stops here; main() still writes the report.
            traceback.print_exc()
            step.status = StepStatus.FAIL
            step.seen.append(str(exc))
            if isinstance(exc, subprocess.CalledProcessError):
                step.seen += (exc.stderr or "").splitlines()
            say(f"  FAIL: {exc}")
            return False
        step.status = StepStatus.PASS
        say("  PASS")
    return True


def hold_timers() -> list[str]:
    """Stop the active acquisition timers and wait out running passes."""
    held = [t for t in HELD_TIMERS if unit_active(t)]
    for timer in held:
        run(["systemctl", "stop", timer], sudo=True)
    for unit in (*HELD_PASSES, TICK_UNIT):
        wait_for_unit_to_end(unit)
    say(f"held timers: {', '.join(held) or 'none were active'}")
    return held


def release_timers(held: list[str]) -> None:
    for timer in held:
        run(["systemctl", "start", timer], sudo=True)
    say(f"released timers: {', '.join(held) or 'none'}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Slice 234 cutover")
    parser.add_argument("ref", help="tag or commit to install (on origin)")
    parser.add_argument("--note", action="append", default=[], help="preamble line")
    args = parser.parse_args(argv[1:])
    checkout = Path.cwd()
    if not (checkout / "scripts" / "cutover_234_tick_wiring.py").exists():
        sys.exit("run from the checkout root")
    preamble = [*(f"- {note}" for note in args.note), ""] if args.note else []
    ctx = Context(args.ref, checkout, preamble=preamble)
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    steps = [Step(n, t, e) for n, (t, e, _a) in enumerate(STEPS, 1)]
    held = hold_timers()
    try:
        passed = run_steps(ctx, steps)
    finally:
        release_timers(held)
    report = checkout / NOTES_DIR / f"{started[:10]}-234-cutover.md"
    outcome = StepStatus.PASS if passed else StepStatus.FAIL
    report.write_text(render_report(steps, started, outcome, ctx.preamble))
    say(f"report written to {report} — {outcome}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
