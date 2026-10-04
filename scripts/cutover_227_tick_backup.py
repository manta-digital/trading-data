#!/usr/bin/env python
"""Enrol the tick cluster in the backup tier on manta9000, in one command.

Slice 227, TD8. Thirteen steps, each recording what it expected against what
it saw; the first failure stops the run and the report is still written to
``project-documents/user/notes/<date>-227-cutover.md``. Recovery is always:
fix the cause, re-run the whole script. Every step is check-then-act, so a
re-run reports what is already done and changes nothing there.

Runs as the cron user (manta) from the checkout root, using ``sudo`` for the
root steps. The tick jobs run exactly as cron runs them: their commands are
read from the installed cron file's ``# cluster 17/tick`` block. Production
``17/main`` is never restarted; its configuration hash is taken before and
after.

    uv run python scripts/cutover_227_tick_backup.py
"""

from __future__ import annotations

import getpass
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from backup_clusters import TABLE_PATH, cluster  # noqa: E402
from cutover_227_helpers import (  # noqa: E402
    CRON_FILE,
    MAIN,
    TICK,
    Step,
    StepFailed,
    health_flags,
    lifecycle_missing,
    production_changes,
    production_lines,
    render_report,
    setup_not_ok,
)
from cutover_227_host import (  # noqa: E402
    BUCKET_KEY,
    RCLONE_REMOTE,
    TICK_UNIT,
    Context,
    conf_sums,
    env_value,
    psql,
    record,
    require,
    setup_backup,
    shell,
    tick_commands,
    tick_locks_held,
)
from cutover_common import NOTES_DIR, run, say  # noqa: E402

#: archive_command normally takes under a second (TD8 step 6).
WAL_SWITCH_WAIT_S = 120
WAL_POLL_S = 2
#: sync_wal_offsite.sh skips segments younger than this (its MIN_AGE, 2m).
PUSH_MIN_AGE_S = 120
ARM_FILE = "RECONCILE-ARMED"
WEEKLY_DONE = "=== weekly backup done"
#: Non-OK setup-backup items a step accepts (anything else stops it).
CHECK_ALLOWED = (r"^(\S+|PENDING RESTART) 17/tick ", r"^(DRIFT|MISSING) cron\.d ")
APPLY_ALLOWED = (r"^PENDING RESTART 17/tick ", r"^MISSING 17/tick arm-file ")


# --- Steps 1–5: guards, provision, setup, restart (TD6) -------------------------


def step_guards(ctx: Context, step: Step) -> None:
    ctx.conf_sums = conf_sums()
    step.seen += ctx.conf_sums.splitlines()
    held = tick_locks_held()
    step.seen.append(f"tick advisory locks held: {held}")
    require(held == 0, step, "a tick run holds its advisory lock")


def step_check(ctx: Context, step: Step) -> None:
    result = setup_backup(ctx, "--check")
    record(step, result)
    require(
        "SUMMARY " in result.stdout, step, "setup-backup --check printed no SUMMARY"
    )
    bad = setup_not_ok(result.stdout, CHECK_ALLOWED)
    require(not bad, step, f"production items not OK: {bad}")
    ctx.old_cron = CRON_FILE.read_text()


def step_provision(ctx: Context, step: Step) -> None:
    result = run(
        [str(ctx.checkout / "scripts" / "provision_tick_cluster.sh")],
        sudo=True,
        check=False,
    )
    record(step, result)
    require(result.returncode == 0, step, "provision_tick_cluster.sh failed")


def step_apply(ctx: Context, step: Step) -> None:
    result = setup_backup(ctx)
    record(step, result)
    require("SUMMARY " in result.stdout, step, "setup-backup printed no SUMMARY")
    bad = setup_not_ok(result.stdout, APPLY_ALLOWED)
    require(not bad, step, f"items not OK after apply: {bad}")
    changes = production_changes(
        ctx.old_cron, CRON_FILE.read_text(), ctx.main.url_key, ctx.bucket_remote
    )
    step.seen += changes or [
        "production cron lines: only the new arguments added (TD3)"
    ]
    require(not changes, step, "production cron lines changed beyond the new arguments")


def step_restart(ctx: Context, step: Step) -> None:
    pending = "SELECT pending_restart FROM pg_settings WHERE name = 'archive_mode'"
    main_pending = psql(MAIN, pending)
    step.seen.append(f"{MAIN} archive_mode pending_restart={main_pending}")
    require(
        main_pending == "f",
        step,
        "production has a pending restart; never restarted here",
    )
    if psql(TICK, pending) == "t":
        held = tick_locks_held()  # re-checked right before the restart (TD6)
        require(held == 0, step, "a tick run holds its advisory lock")
        run(["systemctl", "restart", TICK_UNIT], sudo=True)
        step.seen.append(f"restarted {TICK_UNIT}")
    else:
        step.seen.append("no restart pending on 17/tick")
    mode = psql(TICK, "SHOW archive_mode")
    step.seen.append(f"{TICK} archive_mode={mode}")
    require(mode == "on", step, "tick archive_mode is not on")


# --- Steps 6–9: switch, health, weekly, metadata (TD7) ---------------------------


def step_switch(ctx: Context, step: Step) -> None:
    segment = psql(TICK, "SELECT pg_walfile_name(pg_switch_wal())")
    target = ctx.tick.backup_root / "wal" / f"{segment}.zst"
    step.seen.append(f"switched; waiting for {target}")
    deadline = time.monotonic() + WAL_SWITCH_WAIT_S
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(WAL_POLL_S)
    require(target.exists(), step, f"{target} absent after {WAL_SWITCH_WAIT_S} s")


def run_health(ctx: Context, step: Step) -> tuple[int, int]:
    shell(tick_commands()["health"], step)
    log = ctx.tick.backup_root / "backup-health.log"
    line = log.read_text().splitlines()[-1] if log.exists() else ""
    step.seen.append(line)
    flags = health_flags(line)
    require(flags is not None, step, "no FLAGS summary in the tick health log")
    assert flags is not None
    return flags


def step_health_once(ctx: Context, step: Step) -> None:
    archive, _stale = run_health(ctx, step)
    require(archive == 0, step, "tick archiving is unhealthy")


def step_weekly(ctx: Context, step: Step) -> None:
    root = ctx.tick.backup_root
    arm = root / ARM_FILE
    if (root / "base" / datetime.now().strftime("%Y%m%d")).exists() and arm.exists():
        step.seen.append("base backup for today and arm file exist: skipped (re-run)")
        return
    result = shell(tick_commands()["weekly"], step)
    log = root / "base.log"
    tail = log.read_text().splitlines()[-1] if log.exists() else ""
    step.seen += [f"exit {result.returncode}", tail]
    require(
        result.returncode == 0 and tail.startswith(WEEKLY_DONE),
        step,
        "weekly run failed; not armed",
    )
    arm.touch()
    step.seen.append(f"armed: {arm}")


def step_metadata(ctx: Context, step: Step) -> None:
    result = shell(tick_commands()["metadata"], step)
    step.seen.append(f"exit {result.returncode}")
    require(result.returncode == 0, step, "metadata job failed")


# --- Steps 10–12: verify (TD8) ------------------------------------------------------


def step_health_clean(ctx: Context, step: Step) -> None:
    require(
        run_health(ctx, step) == (0, 0), step, "tick health is not archive=0 stale=0"
    )


def step_offsite(ctx: Context, step: Step) -> None:
    started = time.time()
    shell(tick_commands()["push"], step)
    for sub in ("base", "metadata", "wal"):
        args = ["rclone", "check", "--one-way", str(ctx.tick.backup_root / sub)]
        args += [f"{ctx.tick_remote()}/{sub}", "--exclude", "*.tmp"]
        if sub == "wal":  # the push skips segments younger than its MIN_AGE
            args += ["--min-age", f"{int(time.time() - started) + PUSH_MIN_AGE_S}s"]
        result = run(args, check=False)
        step.seen += [f"$ {' '.join(args)}", *result.stderr.splitlines()[-3:]]
        require(result.returncode == 0, step, f"offsite {sub}/ differs")


def step_production(ctx: Context, step: Step) -> None:
    sums = conf_sums()
    step.seen += sums.splitlines()
    require(sums == ctx.conf_sums, step, "production configuration changed")
    line = (ctx.main.backup_root / "backup-health.log").read_text().splitlines()[-1]
    step.seen.append(f"production health: {line}")
    require(health_flags(line) == (0, 0), step, "production health is not clean")
    result = setup_backup(ctx, "--check")
    record(step, result)
    ctx.lifecycle = lifecycle_missing(result.stdout)
    require(result.returncode == 0, step, "setup-backup --check is not clean")


STEPS: list[tuple[str, str, Callable[[Context, Step], None]]] = [
    ("guards", "production config hashed; no tick run active", step_guards),
    (
        "check",
        "every 17/main item OK; cron.d DRIFT; 17/tick not yet set up",
        step_check,
    ),
    ("provision", "provision_tick_cluster.sh exits 0", step_provision),
    (
        "apply",
        "only tick restart and arm file pending; production lines gain only new args",
        step_apply,
    ),
    ("restart", "tick archive_mode on; production not pending", step_restart),
    (
        "switch",
        f"switched segment archived as .zst within {WAL_SWITCH_WAIT_S} s",
        step_switch,
    ),
    ("health", "tick health FLAGS archive=0", step_health_once),
    ("weekly", "weekly run exits 0 with its done line; arm file created", step_weekly),
    ("metadata", "metadata job exits 0", step_metadata),
    ("health again", "tick health FLAGS archive=0 stale=0", step_health_clean),
    (
        "offsite",
        "rclone check --one-way clean for base/, metadata/, wal/",
        step_offsite,
    ),
    (
        "production",
        "config unchanged; production health clean; setup --check exits 0",
        step_production,
    ),
]


def run_steps(ctx: Context, steps: list[Step]) -> bool:
    for step, (_name, _expected, action) in zip(steps, STEPS, strict=True):
        say(f"Step {step.number}: {step.title}")
        try:
            action(ctx, step)
        except (StepFailed, subprocess.CalledProcessError) as exc:
            # A step's own failure: recorded and reported, the run stops here.
            step.status = "FAIL"
            step.seen.append(str(exc))
            if isinstance(exc, subprocess.CalledProcessError):
                step.seen += (exc.stderr or "").splitlines()
            say(f"  FAIL: {exc}")
            return False
        step.status = "PASS"
        say("  PASS")
    return True


def main() -> int:
    checkout = Path.cwd()
    if not (checkout / "scripts" / "cutover_227_tick_backup.py").exists():
        sys.exit("run from the checkout root")
    # The cron user owns the tick jobs' files; read from production's lines,
    # which exist before and after the cutover.
    user = production_lines(CRON_FILE.read_text())[0].split()[5]
    if getpass.getuser() != user:
        sys.exit(f"run as {user}, the cron user (the tick jobs write its files)")
    bucket = env_value(checkout / ".env", BUCKET_KEY)
    if not bucket:
        sys.exit(f"{BUCKET_KEY} not in {checkout / '.env'}")
    ctx = Context(
        checkout,
        cluster(TABLE_PATH, MAIN),
        cluster(TABLE_PATH, TICK),
        f"{RCLONE_REMOTE}:{bucket}",
    )
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    steps = [
        Step(n, title, expected) for n, (title, expected, _a) in enumerate(STEPS, 1)
    ]
    passed = run_steps(ctx, steps)
    report = checkout / NOTES_DIR / f"{started[:10]}-227-cutover.md"
    outcome = "PASS" if passed else "FAIL"
    report.write_text(render_report(steps, started, outcome, ctx.lifecycle))
    say(f"Step 13: report written to {report} — {outcome}")
    for rule in ctx.lifecycle:
        say(f"  B2 console: {rule}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
