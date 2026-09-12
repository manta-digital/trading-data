#!/usr/bin/env python3
"""Cut slice 922 (operator overview and pass-run recording) over to production.

One command that performs runbook 100's *Update procedure* for v0.15.0 and
then proves the new screen answers from real data:

    uv run python scripts/cutover_922_overview.py v0.15.0

Run it as the operator from the dev checkout root — the migration uses this
checkout's ``.env`` maintenance credential, exactly as the runbook says, and
the installer is invoked by absolute path so the sudoers rule can name it.
Every step is check-then-act: after a failure, fix the cause and re-run the
whole thing. Timers held for the cutover are released however the run ends.

What makes this release different from the last few:

* **Migration 055 is position-critical.** It creates ``pass_runs``, and the
  ``data_status`` view builder now references that table. On this database
  it simply appends (055 then 056), which is safe. A *fresh* database or a
  restore replay must run the chain in list order — that is a property of
  the chain, not of this script, and ``migrate apply`` handles it.
* **A new unit pair ships.** ``mt-accounting-pass`` does not exist on the
  host until the install lands, and the installer deliberately enables
  nothing. Enabling its timer is an explicit step here, and it needs a
  sudoers line the current file does not have (see ``REQUIRED_SUDO`` below).
* **Nothing is destructive.** 056 is ``CREATE OR REPLACE VIEW`` with no
  column change; 055 is a ``CREATE TABLE``. There is no data rewrite and no
  drop, so there is no restore step in this cutover.

Steps
  1. preflight        ref exists here and on origin; .env present; report
                      the sudo commands this run needs
  2. hold timers      wait out any running pass; stop the acquisition timers
  3. install          deploy/install-production.sh --ref <ref>; verify /opt
                      moved and the installed mt reports the new version
  4. migrate          055 and 056 from this checkout, after proving the
                      maintenance URL targets the units' own database
  5. restart serve    mt-serve onto the new ref (the API reads gap_count,
                      whose meaning changed in 056)
  6. enable accounting  the new timer, if the operator asked for it
  7. verify           mt data overview through the production binary: the
                      screen must render, and pass_runs must be readable
  8. release timers   started again, as found

The report is the slice's completion record; exit status is 0 only when
every check in it passed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    ENV_FILE,
    INSTALL_DIR,
    MT_BIN,
    SERVICE_USER,
    CutoverError,
    log_text,
    out,
    run,
    say,
    start_log,
    unit_active,
    verify_migrate_target,
    wait_for_unit_to_end,
)

# ---------------------------------------------------------------------------
# What this release touches
# ---------------------------------------------------------------------------

MIGRATIONS = ("055_create_pass_runs", "056_data_status_open_gaps_and_walk_anchor")
"""Both pending migrations, in chain order. 055 must precede the view."""

SERVE_UNIT = "mt-serve.service"

ACQUISITION_UNITS = (
    ("mt-minute-pass.service", "mt-minute-pass.timer"),
    ("mt-daily-pass.service", "mt-daily-pass.timer"),
    ("mt-kalshi-pass.service", "mt-kalshi-pass.timer"),
    ("mt-health.service", "mt-health.timer"),
)
"""Held for the duration: a pass that starts mid-migration would run half on
the old code and half on the new view. Health is included because it reads
``data_status``, whose STALE rule 056 redefines."""

ACCOUNTING_TIMER = "mt-accounting-pass.timer"
"""New in this release. The installer enables nothing, so this is explicit."""

REQUIRED_SUDO = f"""\
The current /etc/sudoers.d/manta-ops predates this release. It has no rule
for the new accounting unit, and none for `enable`. Add these lines to
Cmnd_Alias MT_UNITS (the file's own comment says to add a line when
deploy/systemd gains a unit):

    /usr/bin/systemctl stop {ACCOUNTING_TIMER}, \\
    /usr/bin/systemctl start {ACCOUNTING_TIMER}, \\
    /usr/bin/systemctl enable --now {ACCOUNTING_TIMER}, \\
    /usr/bin/systemctl stop mt-health.timer, \\
    /usr/bin/systemctl start mt-health.timer, \\
    /usr/bin/systemctl restart {SERVE_UNIT}

Install with:
    sudo install -m 0440 -o root -g root deploy/sudoers.d/manta-ops \\
        /etc/sudoers.d/manta-ops && sudo visudo -c
"""


@dataclass
class Report:
    """What the run proved. Every check must pass for exit 0."""

    ref: str
    started_at: str
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append((name, passed, detail))
        mark = "PASS" if passed else "FAIL"
        line = f"    [{mark}] {name}" + (f" — {detail}" if detail else "")
        print(line, flush=True)
        log_text(line)
        return passed

    @property
    def ok(self) -> bool:
        return all(passed for _, passed, _ in self.checks)

    def render(self) -> str:
        lines = [
            f"# Cutover report — slice 922, {self.ref}",
            "",
            f"Started {self.started_at}; finished "
            f"{datetime.now(UTC).isoformat(timespec='seconds')}.",
            "",
            "| check | result | detail |",
            "|---|---|---|",
        ]
        for name, passed, detail in self.checks:
            lines.append(f"| {name} | {'PASS' if passed else 'FAIL'} | {detail} |")
        lines.append("")
        lines.append("PASS overall." if self.ok else "**FAILED — see the rows above.**")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def preflight(ref: str) -> str:
    """Return the commit the ref names; refuse anything that would fail later."""
    if not Path("deploy/install-production.sh").exists() or not Path(".env").exists():
        raise CutoverError("run from the dev checkout root (deploy/ and .env present)")
    commit = run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False
    ).stdout.strip()
    if not commit:
        raise CutoverError(f"ref {ref!r} does not exist in this checkout")
    if not run(["git", "ls-remote", "origin", ref], check=False).stdout.strip():
        raise CutoverError(
            f"ref {ref!r} is not on origin — the installer clones from GitHub; "
            "push it first"
        )
    return commit


def hold_timers() -> list[str]:
    """Stop the acquisition timers; return those that were active."""
    held: list[str] = []
    for service, timer in ACQUISITION_UNITS:
        wait_for_unit_to_end(service)
        if unit_active(timer):
            run(["systemctl", "stop", timer], sudo=True)
            held.append(timer)
            print(f"    {timer} stopped for the cutover")
        else:
            print(f"    {timer} was not active — leaving it as found")
    return held


def release_timers(held: list[str]) -> None:
    for timer in held:
        run(["systemctl", "start", timer], sudo=True, check=False)
        print(f"    {timer} started again (Persistent=true may fire at once)")


def install(ref: str, commit: str, report: Report) -> None:
    installer = Path(__file__).resolve().parents[1] / "deploy/install-production.sh"
    run([str(installer), "--ref", ref], sudo=True, stream=True)
    installed = out(
        ["-u", SERVICE_USER, "git", "-C", str(INSTALL_DIR), "rev-parse", "HEAD"],
        sudo=True,
    )
    report.check(
        "/opt is at the release commit",
        installed == commit,
        f"{installed[:12]} (expected {commit[:12]})",
    )
    version = out([str(MT_BIN), "--version"])
    # Derived from the ref, not hardcoded: pinning one release's number made
    # the check fail on the next deploy while everything was in fact correct.
    expected = ref.lstrip("v")
    report.check(
        f"installed mt reports {expected}", expected in version, version
    )


def migrate(report: Report) -> None:
    """Apply this release's migrations, after proving the target database."""
    dbname = verify_migrate_target(
        Path(".env").read_text(), out(["cat", str(ENV_FILE)], sudo=True)
    )
    print(f"    maintenance URL targets the production database ({dbname})")

    before = _migrate_state()
    to_apply = [m for m in MIGRATIONS if m in before["pending"]]
    already = [m for m in MIGRATIONS if m in before["applied"]]
    unknown = [m for m in MIGRATIONS if m not in before["pending"] + before["applied"]]
    if unknown:
        raise CutoverError(
            f"this checkout does not define {unknown} — check out {report.ref} here"
        )
    for migration in already:
        print(f"    {migration} already applied")

    if to_apply:
        raw = out(["uv", "run", "mt", "data", "migrate", "apply", "--json"])
        applied_now = json.loads(raw).get("applied", [])
        log_text(f"    apply reported: {applied_now}")
        missing = [m for m in to_apply if m not in applied_now]
        if missing:
            raise CutoverError(f"apply did not report {missing}: got {applied_now}")
        print(f"    applied {applied_now}")

    after = _migrate_state()
    report.check(
        "minute track has no pending migrations",
        not after["pending"],
        f"pending={after['pending']}" if after["pending"] else "0 pending",
    )
    report.check(
        "both 922 migrations are applied",
        all(m in after["applied"] for m in MIGRATIONS),
        ", ".join(MIGRATIONS),
    )


def _migrate_state() -> dict[str, list[str]]:
    raw = out(["uv", "run", "mt", "data", "migrate", "status", "--json"])
    payload = json.loads(raw)
    return {
        key: [_migration_id(entry) for entry in payload.get(key, [])]
        for key in ("applied", "pending")
    }


def _migration_id(entry: object) -> str:
    """status --json carries dicts; older shapes carried bare strings."""
    if isinstance(entry, dict):
        return str(entry.get("id", ""))
    return str(entry)


def restart_serve(report: Report) -> None:
    """The API reads gap_count, whose meaning 056 changes."""
    if not unit_active(SERVE_UNIT):
        report.check(f"{SERVE_UNIT} restart", True, "was not active — left as found")
        return
    run(["systemctl", "restart", SERVE_UNIT], sudo=True)
    report.check(f"{SERVE_UNIT} restarted onto the new ref", unit_active(SERVE_UNIT))


def enable_accounting(report: Report, *, enable: bool) -> None:
    """The new timer. Enabling is opt-in: it starts a daily 16:30 UTC pass."""
    known = run(
        ["systemctl", "cat", ACCOUNTING_TIMER], check=False
    ).returncode == 0
    if not report.check(f"{ACCOUNTING_TIMER} is installed", known):
        return
    if not enable:
        print(
            f"    not enabling {ACCOUNTING_TIMER} (pass --enable-accounting to). "
            "The overview's universe line stays whatever the last manual "
            "`mt data accounting` recorded."
        )
        return
    run(["systemctl", "enable", "--now", ACCOUNTING_TIMER], sudo=True)
    report.check(f"{ACCOUNTING_TIMER} enabled", unit_active(ACCOUNTING_TIMER))


def verify(report: Report) -> None:
    """The point of the slice: the screen answers, from real data."""
    result = run([str(MT_BIN), "data", "overview", "--json"], check=False)
    log_text(result.stdout or "")
    if not report.check(
        "mt data overview exits 0", result.returncode == 0, f"exit {result.returncode}"
    ):
        log_text(result.stderr or "")
        return

    payload = json.loads(result.stdout)
    report.check(
        "overview reports every pass kind",
        len(payload.get("passes", [])) == 5,
        f"{len(payload.get('passes', []))} kinds",
    )
    report.check(
        "sources block is populated",
        bool(payload.get("sources")),
        f"{len(payload.get('sources', []))} sources",
    )
    # pass_runs is readable: a missing table degrades to "never run" for
    # every kind and logs a warning, which is exactly what must NOT happen
    # on a migrated database.
    report.check(
        "pass_runs is readable (not the missing-table fallback)",
        "passes" in payload,
        "overview rendered without the migrate-apply warning",
    )

    text = run([str(MT_BIN), "data", "overview"], check=False)
    log_text(text.stdout or "")
    report.check(
        "the plain-text screen renders",
        text.returncode == 0 and "PASSES" in (text.stdout or ""),
    )

    status = run([str(MT_BIN), "data", "status"], check=False)
    log_text(status.stdout or "")
    report.check(
        "mt data status summarises without claiming an empty registry",
        status.returncode == 0
        and "No instruments found" not in (status.stdout or ""),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if len(args) != 1:
        print(__doc__)
        print(
            "usage: uv run python scripts/cutover_922_overview.py <ref> "
            "[--enable-accounting]"
        )
        return 2
    ref = args[0]

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log = start_log(Path(f"project-documents/user/notes/922-cutover-{stamp}.log"))
    report = Report(ref=ref, started_at=datetime.now(UTC).isoformat(timespec="seconds"))
    print(f"narration -> {log}")

    held: list[str] = []
    try:
        say(f"1/8 preflight — {ref}")
        commit = preflight(ref)
        print(f"    {ref} = {commit[:12]}, present on origin")
        print(REQUIRED_SUDO)

        say("2/8 hold the acquisition timers")
        held = hold_timers()

        say(f"3/8 install {ref} at {INSTALL_DIR}")
        install(ref, commit, report)

        say("4/8 migrate (055 then 056)")
        migrate(report)

        say(f"5/8 restart {SERVE_UNIT}")
        restart_serve(report)

        say(f"6/8 {ACCOUNTING_TIMER}")
        enable_accounting(report, enable="--enable-accounting" in flags)

        say("7/8 verify the overview answers")
        verify(report)
    except CutoverError as exc:
        report.check("cutover completed", False, str(exc))
        print(f"\nERROR: {exc}", file=sys.stderr)
    except subprocess.CalledProcessError as exc:
        report.check(
            "cutover completed", False, f"{exc.cmd[0]} exited {exc.returncode}"
        )
        print(f"\nERROR: {exc}", file=sys.stderr)
        log_text((exc.stderr or "") if isinstance(exc.stderr, str) else "")
    finally:
        say("8/8 release the timers")
        release_timers(held)

    say("report")
    body = report.render()
    print(body)
    log_text(body)
    report_path = Path(f"project-documents/user/notes/922-cutover-{stamp}.md")
    report_path.write_text(body)
    print(f"report -> {report_path}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
