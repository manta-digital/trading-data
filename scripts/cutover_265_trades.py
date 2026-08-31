#!/usr/bin/env python3
"""Cut slice 265 (Kalshi public trades) over to production on manta9000.

One command that performs runbook 100's *Update procedure* for this release
and then records the first supervised firing — everything in task file
``265-tasks.public-trades-collection-2.md`` Section 9 that sits between the
Project Manager's two manual acts: tagging the release before, and reading
the report after.

    uv run python scripts/cutover_265_trades.py v0.11.0

Run it as the operator from the dev checkout root (the migration uses this
checkout's ``.env`` maintenance credential, exactly as the runbook says).
It uses ``sudo`` for the privileged steps: one password prompt at the start
and possibly one more after the ~15-minute firing. Every step is
check-then-act, so after a failure you fix the cause and re-run; the timer is
released again no matter how the run ends.

Steps
  1. hold the timer       wait out a running Kalshi pass; stop mt-kalshi-pass.timer
  2. install the ref      deploy/install-production.sh --ref <ref>; verify /opt moved
  3. rename the settings  MT_KALSHI_CANDLE_* -> MT_KALSHI_COLLECTION_* in
                          /etc/manta-trading.env (backup kept)
  4. migrate              mt data migrate apply --track kalshi (from this checkout),
                          then prove the guard passes: `status` on the new binary
                          (which reads a column this migration adds — order matters)
  5. first firing         sudo mt-run kalshi, streamed live (journal cursor taken first)
  6. report               Section 9's numbers from that one firing -> user/notes
  7. release the timer    start mt-kalshi-pass.timer again (Persistent=true may
                          fire a missed schedule at once)

The report is the slice's completion record; exit status is 0 only when
every check in it passed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from manta_trading.config import (
    KALSHI_COLLECTION_ENV_PREFIX,
    RENAMED_KALSHI_CANDLE_ENV_PREFIX,
)
from manta_trading.data.kalshi.constants import TRADE_REQUESTS_PER_PASS
from manta_trading.market.schema.migrations.kalshi import KALSHI_MIGRATIONS

# Host names and paths — the same single source deploy/install-production.sh
# and deploy/mt-run use; they are host facts, not application values.
INSTALL_DIR = Path("/opt/manta-trading")
MT_BIN = INSTALL_DIR / ".venv/bin/mt"
ENV_FILE = Path("/etc/manta-trading.env")
SERVICE_USER = "manta-trading"
PASS_UNIT = "mt-kalshi-pass.service"
PASS_TIMER = "mt-kalshi-pass.timer"
SERVE_UNIT = "mt-serve.service"
MIGRATION_TRACK = "kalshi"
#: The migration this release adds — the last one on the kalshi track.
MIGRATION_ID = KALSHI_MIGRATIONS[-1]["id"]
NOTES_DIR = Path("project-documents/user/notes")

#: Rehearsal figure (user/notes/2026-08-30-265-rehearsal.md): first pass,
#: insert path, uncompressed — the comparison Task 9.2 asks for.
REHEARSAL_SECONDS_PER_PAGE = 0.21
#: "Minutes rather than seconds" (design step 10): a window slower than this
#: is the signal to pause the compression policy for the drain.
SLOW_WINDOW_SECONDS = 300
#: How often to look for a running pass to end.
POLL_SECONDS = 15

PHASE_STARTED = re.compile(
    r"kalshi trades phase started run_id=(?P<run_id>\S+) cutoff=(?P<cutoff>\S+) "
    r"watermark=(?P<watermark>\S+) coverage_from=(?P<coverage_from>\S+) "
    r"rule: (?P<rule>.*)"
)
FIRST_RUN = "kalshi trades first run: the stored tape starts at the cutoff"
WINDOW = re.compile(
    r"trades window (?P<start>\S+)→(?P<end>\S+) pages (?P<pages>\d+) "
    r"fetched (?P<fetched>\d+) written (?P<written>\d+) unknown (?P<unknown>\d+) "
    r"excluded (?P<excluded>\d+)"
)
PASS_FINISHED = re.compile(
    r"kalshi pass finished outcome=(?P<outcome>\S+) duration=(?P<ms>\d+) ms "
    r"phases: (?P<phases>.*)"
)
SUMMARY_HEADER = "Kalshi trades"
SUMMARY_REQUESTS = re.compile(r"requests (?P<requests>[\d,]+)(?P<capped> \(capped\))?")
SUMMARY_WATERMARK = re.compile(r"watermark\s+(?P<before>\S+) → (?P<after>\S+)")
HTTP_429 = "kalshi HTTP 429"
HTTP_TRANSIENT = "kalshi HTTP "
TRANSPORT_ERROR = "kalshi transport error"


class CutoverError(RuntimeError):
    """A step found the host in a state it will not act on."""


def say(text: str) -> None:
    print(f"\n==> {text}", flush=True)


def run(
    args: list[str], *, sudo: bool = False, check: bool = True, stream: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a command; ``stream`` leaves stdout/stderr on the terminal."""
    cmd = (["sudo", *args]) if sudo else args
    return subprocess.run(cmd, check=check, text=True, capture_output=not stream)


def out(args: list[str], *, sudo: bool = False) -> str:
    return run(args, sudo=sudo).stdout.strip()


def unit_active(unit: str) -> bool:
    state = run(["systemctl", "is-active", unit], check=False).stdout.strip()
    return state in {"active", "activating"}


def wait_for_pass_to_end() -> None:
    while unit_active(PASS_UNIT):
        print(f"    a Kalshi pass is running — waiting ({POLL_SECONDS}s) …", flush=True)
        time.sleep(POLL_SECONDS)


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
    remote = run(["git", "ls-remote", "origin", ref], check=False).stdout.strip()
    if not remote:
        raise CutoverError(
            f"ref {ref!r} is not on origin — the installer clones from GitHub; "
            "push it first"
        )
    run(["sudo", "-v"], stream=True)
    return commit


def hold_timer() -> bool:
    """Stop the timer for the duration; return whether it was active."""
    was_active = unit_active(PASS_TIMER)
    wait_for_pass_to_end()
    if was_active:
        run(["systemctl", "stop", PASS_TIMER], sudo=True)
        print(f"    {PASS_TIMER} stopped for the cutover (restarted at the end)")
    else:
        print(f"    {PASS_TIMER} was not active — leaving it as found")
    return was_active


def install(ref: str, commit: str) -> str:
    run(["deploy/install-production.sh", "--ref", ref], sudo=True, stream=True)
    installed = out(
        ["-u", SERVICE_USER, "git", "-C", str(INSTALL_DIR), "rev-parse", "HEAD"],
        sudo=True,
    )
    if installed != commit:
        raise CutoverError(f"{INSTALL_DIR} is at {installed}, expected {commit}")
    version = out([str(MT_BIN), "--version"])
    print(f"    installed {ref} = {commit[:12]}; {version}")
    return version


def rename_settings() -> list[str]:
    """Rename every old-prefix name in the env file; return the names renamed."""
    content = out(["cat", str(ENV_FILE)], sudo=True)  # never printed
    names = sorted(
        set(re.findall(rf"{RENAMED_KALSHI_CANDLE_ENV_PREFIX}[A-Z0-9_]+", content))
    )
    if names:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = f"{ENV_FILE}.bak-265-{stamp}"
        run(["cp", "-p", str(ENV_FILE), backup], sudo=True)
        run(
            [
                "sed",
                "-i",
                f"s/{RENAMED_KALSHI_CANDLE_ENV_PREFIX}/{KALSHI_COLLECTION_ENV_PREFIX}/g",
                str(ENV_FILE),
            ],
            sudo=True,
        )
        left = out(["cat", str(ENV_FILE)], sudo=True).count(
            RENAMED_KALSHI_CANDLE_ENV_PREFIX
        )
        if left:
            raise CutoverError(
                f"{left} old-name occurrence(s) remain in {ENV_FILE} after the rename"
            )
        print(f"    renamed in {ENV_FILE} (backup {backup}): {', '.join(names)}")
    else:
        print(
            f"    no {RENAMED_KALSHI_CANDLE_ENV_PREFIX}* names in {ENV_FILE} — "
            "nothing to rename"
        )
    return names


def production_status() -> dict:
    """``mt data kalshi status --json`` through the production front door."""
    raw = out(["mt-run", "data", "kalshi", "status", "--json"], sudo=True)
    return json.loads(raw)


def prove_guard_passes() -> dict:
    """``status`` on the new binary: the rename guard and the schema both hold.

    Runs after the migration — ``read_trade_status`` selects
    ``sync_state.coverage_from_ts``, which ``kalshi_006_trades`` adds.
    """
    status = production_status()
    rule = (status.get("candles") or {}).get("rule", {}).get("description", "?")
    print(f"    guard passes on the new binary; rule in force: {rule}")
    if status.get("trades") is not None:
        print(
            "    NOTE: trades state already exists — this will not be the first firing"
        )
    if unit_active(SERVE_UNIT):
        run(["systemctl", "restart", SERVE_UNIT], sudo=True)
        print(f"    {SERVE_UNIT} restarted onto the new ref (runbook update procedure)")
    return status


def migrate() -> None:
    def status() -> tuple[list[str], list[str]]:
        raw = out(
            [
                "uv",
                "run",
                "mt",
                "data",
                "migrate",
                "status",
                "--track",
                MIGRATION_TRACK,
                "--json",
            ]
        )
        state = json.loads(raw)
        if not state.get("connected", False):
            raise CutoverError(
                f"migrate status could not connect: {state.get('error')}"
            )
        return (
            [e["id"] for e in state.get("applied", [])],
            [e["id"] for e in state.get("pending", [])],
        )

    applied, pending = status()
    if MIGRATION_ID in applied:
        print(f"    {MIGRATION_ID} already applied")
    elif MIGRATION_ID in pending:
        raw = out(
            [
                "uv",
                "run",
                "mt",
                "data",
                "migrate",
                "apply",
                "--track",
                MIGRATION_TRACK,
                "--json",
            ]
        )
        applied_now = json.loads(raw).get("applied", [])
        if MIGRATION_ID not in applied_now:
            raise CutoverError(f"apply did not report {MIGRATION_ID}: {applied_now}")
        print(f"    applied {applied_now}")
    else:
        raise CutoverError(
            f"this checkout does not define {MIGRATION_ID} (applied={applied}, "
            f"pending={pending}) — check out the release ref here first"
        )
    _, pending = status()
    if pending:
        raise CutoverError(f"kalshi track still pending after apply: {pending}")
    print(f"    {MIGRATION_TRACK} track: 0 pending")


def journal_cursor() -> str:
    text = out(["journalctl", "-u", PASS_UNIT, "-n", "0", "--show-cursor", "-q"])
    match = re.search(r"-- cursor: (\S+)", text)
    if not match:
        raise CutoverError(f"could not read a journal cursor from: {text!r}")
    return match.group(1)


def fire() -> tuple[str, str]:
    cursor = journal_cursor()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    print(
        "    sudo mt-run kalshi — streaming; Ctrl-C only detaches, "
        "the script keeps waiting"
    )
    run(["mt-run", "kalshi"], sudo=True, check=False, stream=True)
    wait_for_pass_to_end()
    run(["sudo", "-v"], stream=True)  # the firing may outlive the sudo grace period
    return cursor, started


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class Firing:
    entries: list[tuple[datetime, str]]
    checks: list[tuple[bool, str]] = field(default_factory=list)

    def check(self, ok: bool, text: str) -> None:
        self.checks.append((ok, text))

    def find(self, needle: str) -> list[tuple[datetime, str]]:
        return [(ts, m) for ts, m in self.entries if needle in m]

    def summary_block(self) -> str:
        """The pass's ``Kalshi trades`` block, re-joined (Rich may wrap at 80 cols)."""
        messages = [m for _, m in self.entries]
        for i, m in enumerate(messages):
            if m.strip() == SUMMARY_HEADER:
                block = []
                for line in messages[i + 1 : i + 12]:
                    block.append(line.strip())
                    if line.strip().startswith("cutoff"):
                        break
                return "\n".join(block)
        return ""

    def windows(self) -> list[dict]:
        rows = []
        previous: datetime | None = None
        for ts, m in self.entries:
            if PHASE_STARTED.search(m):
                previous = ts
            w = WINDOW.search(m)
            if w and previous is not None:
                seconds = (ts - previous).total_seconds()
                pages = int(w["pages"])
                rows.append(
                    {
                        **w.groupdict(),
                        "seconds": seconds,
                        "per_page": seconds / pages if pages else 0.0,
                    }
                )
            if w:
                previous = ts
        return rows


def read_journal(cursor: str) -> Firing:
    raw = out(
        [
            "journalctl",
            "-u",
            PASS_UNIT,
            f"--after-cursor={cursor}",
            "-o",
            "json",
            "--no-pager",
            "-q",
        ]
    )
    entries = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        message = e.get("MESSAGE", "")
        if isinstance(message, list):  # journald encodes non-UTF-8 as byte arrays
            message = bytes(message).decode("utf-8", "replace")
        ts = datetime.fromtimestamp(int(e["__REALTIME_TIMESTAMP"]) / 1_000_000, UTC)
        entries.append((ts, message))
    return Firing(entries)


def fmt_num(text: str) -> int:
    return int(text.replace(",", ""))


def build_report(
    ref: str, commit: str, version: str, renamed: list[str], cursor: str, started: str
) -> tuple[str, bool]:
    firing = read_journal(cursor)
    result = out(["systemctl", "show", PASS_UNIT, "-p", "Result", "--value"])
    exit_status = out(
        ["systemctl", "show", PASS_UNIT, "-p", "ExecMainStatus", "--value"]
    )
    status = production_status()
    trades = status.get("trades") or {}
    lines: list[str] = []
    add = lines.append

    # --- Task 9.2: the pass and the first-run floor -------------------------
    finished = next(
        (PASS_FINISHED.search(m) for _, m in firing.entries if PASS_FINISHED.search(m)),
        None,
    )
    phases = finished["phases"] if finished else "(no `kalshi pass finished` line)"
    firing.check(
        result == "success" and exit_status == "0",
        f"unit Result={result} ExecMainStatus={exit_status}",
    )
    firing.check(
        bool(finished)
        and "trades=ok" in phases
        and "catalog=ok" in phases
        and "candles=ok" in phases,
        f"phases: {phases}",
    )
    started_line = next(
        (PHASE_STARTED.search(m) for _, m in firing.entries if PHASE_STARTED.search(m)),
        None,
    )
    first_run = bool(firing.find(FIRST_RUN))
    if started_line:
        cutoff, wm, cov = (
            started_line["cutoff"],
            started_line["watermark"],
            started_line["coverage_from"],
        )
        firing.check(
            first_run and cutoff == wm == cov,
            f"first-run floor is the cutoff (Criterion 6): first_run={first_run} "
            f"cutoff={cutoff} watermark={wm} coverage_from={cov}",
        )
    else:
        cutoff = wm = cov = "?"
        firing.check(False, "no `kalshi trades phase started` line in this firing")

    # --- Task 9.3: five numbers from this one firing -------------------------
    block = firing.summary_block()
    req = SUMMARY_REQUESTS.search(block)
    wmk = SUMMARY_WATERMARK.search(block)
    requests = fmt_num(req["requests"]) if req else -1
    capped = bool(req and req["capped"])
    windows = firing.windows()
    advance_h = "?"
    if wmk:
        try:
            before = datetime.fromisoformat(wmk["before"])
            after = datetime.fromisoformat(wmk["after"])
            advance_h = f"{(after - before).total_seconds() / 3600:.2f}"
        except ValueError:
            advance_h = f"unparsed ({wmk['before']} → {wmk['after']})"
    firing.check(
        bool(wmk) and len(windows) > 0,
        f"(1) watermark advanced {advance_h} h over {len(windows)} windows "
        f"({wmk['before'] if wmk else '?'} → {wmk['after'] if wmk else '?'})",
    )
    firing.check(
        requests >= TRADE_REQUESTS_PER_PASS and capped,
        f"(2) requests {requests:,} capped={capped} "
        f"(cap {TRADE_REQUESTS_PER_PASS:,}, Criterion 8)",
    )
    n429 = len(firing.find(HTTP_429))
    n_transient = len(firing.find(HTTP_TRANSIENT))
    n_transport = len(firing.find(TRANSPORT_ERROR))
    firing.check(
        n429 == 0,
        f"(3) HTTP 429 retries {n429} (all transient-status retries "
        f"{n_transient}, transport errors {n_transport})",
    )
    before_cov = trades.get("before_coverage")
    firing.check(
        before_cov is not None,
        f"(4) before coverage baseline (266's input): {before_cov:,}"
        if before_cov is not None
        else "(4) status has no trades block",
    )
    if windows:
        slowest = max(windows, key=lambda w: w["seconds"])
        firing.check(
            slowest["seconds"] < SLOW_WINDOW_SECONDS,
            f"(5) slowest window {slowest['seconds']:.0f} s for {slowest['pages']} "
            f"pages ({slowest['per_page']:.2f} s/page; rehearsal "
            f"{REHEARSAL_SECONDS_PER_PAGE} s/page; pause-the-policy threshold "
            f"{SLOW_WINDOW_SECONDS} s) {slowest['start']}→{slowest['end']}",
        )
    else:
        firing.check(False, "(5) no `trades window` lines in this firing")

    all_ok = all(ok for ok, _ in firing.checks)
    today = datetime.now(UTC).strftime("%Y%m%d")
    add("---")
    add("docType: note")
    add("project: trading-data")
    add("slice: 265-slice.public-trades-collection")
    add(f"dateCreated: {today}")
    add(f"dateUpdated: {today}")
    add(f"status: {'complete' if all_ok else 'in_progress'}")
    add("---")
    add("")
    add("# Slice 265 cutover — first supervised firing on manta9000")
    add("")
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    add(
        f"Generated by `scripts/cutover_265_trades.py` at {generated}; "
        f"firing started {started}."
    )
    add("")
    add("## Task 9.1 — host migrated")
    add("")
    add(f"- installed ref `{ref}` = `{commit}`; `{version}`")
    names = ", ".join(f"`{n}`" for n in renamed) if renamed else "none were set"
    add(f"- env names renamed: {names}")
    add(f"- `{MIGRATION_ID}` applied; `{MIGRATION_TRACK}` track 0 pending")
    add("")
    add("## Task 9.2 — first supervised firing (Criterion 13, first half; Criterion 6)")
    add("")
    for ok, text in firing.checks[:3]:
        add(f"- {'✅' if ok else '❌'} {text}")
    add("")
    add("## Task 9.3 — the five numbers from this one firing")
    add("")
    for ok, text in firing.checks[3:]:
        add(f"- {'✅' if ok else '❌'} {text}")
    add("")
    add("## Pass summary block (journal)")
    add("")
    add("```")
    add(block or "(no `Kalshi trades` block found after the cursor)")
    add("```")
    add("")
    add("## Windows (wall time from journal timestamps)")
    add("")
    add(
        "| window | pages | seconds | s/page | fetched | written | unknown | excluded |"
    )
    add("|---|---:|---:|---:|---:|---:|---:|---:|")
    for w in windows:
        add(
            f"| {w['start']}→{w['end']} | {w['pages']} | {w['seconds']:.0f} "
            f"| {w['per_page']:.2f} | {int(w['fetched']):,} | {int(w['written']):,} "
            f"| {int(w['unknown']):,} | {int(w['excluded']):,} |"
        )
    add("")
    add("## `mt-run data kalshi status --json` → `.trades` after the firing")
    add("")
    add("```json")
    add(json.dumps(trades, indent=2))
    add("```")
    add("")
    add("## Handoff — the steady state (not a task)")
    add("")
    add(
        "`tape through` should advance ~7 h per hourly firing until `behind` "
        "clears (~10 days), then stay within two hours of now; `before coverage` "
        "must not move. Slice 266 does not start against a draining tape."
    )
    return "\n".join(lines) + "\n", all_ok


# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 2
    ref = argv[1]
    say(f"preflight: ref {ref}")
    commit = preflight(ref)
    timer_was_active = False
    try:
        say("1/7 hold the timer")
        timer_was_active = hold_timer()
        say(f"2/7 install {ref} at {INSTALL_DIR}")
        version = install(ref, commit)
        say(
            f"3/7 rename {RENAMED_KALSHI_CANDLE_ENV_PREFIX}* → "
            f"{KALSHI_COLLECTION_ENV_PREFIX}*"
        )
        renamed = rename_settings()
        say(f"4/7 migrate the {MIGRATION_TRACK} track")
        migrate()
        prove_guard_passes()
        say("5/7 first supervised firing")
        cursor, started = fire()
        say("6/7 report")
        report, all_ok = build_report(ref, commit, version, renamed, cursor, started)
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        path = NOTES_DIR / f"{datetime.now(UTC).strftime('%Y-%m-%d')}-265-cutover.md"
        path.write_text(report)
        print(report)
        print(f"    written to {path}")
    finally:
        say("7/7 release the timer")
        if timer_was_active:
            run(["systemctl", "start", PASS_TIMER], sudo=True)
            print(
                f"    {PASS_TIMER} started (Persistent=true: a missed :20 fires "
                "now — a normal pass)"
            )
        else:
            print(f"    {PASS_TIMER} left as found (was not active)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except CutoverError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(
            f"\nERROR: {' '.join(exc.cmd)} exited {exc.returncode}\n{exc.stderr or ''}",
            file=sys.stderr,
        )
        sys.exit(1)
