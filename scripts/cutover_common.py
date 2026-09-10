"""Host-side helpers shared by the Kalshi cutover scripts (slice 267).

Extracted from ``cutover_265_trades.py`` (left untouched as the record of
that release) so ``cutover_267_historical.py`` does not re-spell them. Every
function here talks to manta9000 — systemd, the installer, the journal, the
production ``mt-run`` front door — and nothing here knows a slice's checks.
Run from the dev checkout root; ``sudo`` is used for the privileged steps.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
NOTES_DIR = Path("project-documents/user/notes")
#: How often to look for a running pass to end.
POLL_SECONDS = 15


class CutoverError(RuntimeError):
    """A step found the host in a state it will not act on."""


_LOG_FILE: Path | None = None


def start_log(path: Path) -> Path:
    """Tee every ``say`` line into ``path`` (slice 921 / #22).

    The first 921 cutover's only record was terminal scrollback across two
    ten-hour firings. The narration is the cutover's report; it gets a file.
    """
    global _LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"cutover log opened {datetime.now(UTC).isoformat()}\n")
    _LOG_FILE = path
    return path


def say(text: str) -> None:
    line = f"\n==> {text}"
    print(line, flush=True)
    log_text(line)


def log_text(text: str) -> None:
    """Append raw text (a subprocess's output) to the narration file, if open."""
    if _LOG_FILE is not None:
        with _LOG_FILE.open("a") as handle:
            handle.write(text.rstrip("\n") + "\n")


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


def release_timer(was_active: bool) -> None:
    if was_active:
        run(["systemctl", "start", PASS_TIMER], sudo=True)
        print(f"    {PASS_TIMER} started again (Persistent=true may fire at once)")
    else:
        print(f"    {PASS_TIMER} left inactive, as found")


def install(ref: str, commit: str) -> str:
    # Absolute so the sudoers rule (deploy/sudoers.d/manta-ops) can name it.
    installer = Path(__file__).resolve().parents[1] / "deploy/install-production.sh"
    run([str(installer), "--ref", ref], sudo=True, stream=True)
    installed = out(
        ["-u", SERVICE_USER, "git", "-C", str(INSTALL_DIR), "rev-parse", "HEAD"],
        sudo=True,
    )
    if installed != commit:
        raise CutoverError(f"{INSTALL_DIR} is at {installed}, expected {commit}")
    version = out([str(MT_BIN), "--version"])
    print(f"    installed {ref} = {commit[:12]}; {version}")
    return version


def production_status() -> dict[str, Any]:
    """``mt data kalshi status --json`` through the production front door."""
    raw = out(["mt-run", "data", "kalshi", "status", "--json"], sudo=True)
    return json.loads(raw)


def restart_serve_if_active() -> None:
    if unit_active(SERVE_UNIT):
        run(["systemctl", "restart", SERVE_UNIT], sudo=True)
        print(f"    {SERVE_UNIT} restarted onto the new ref (runbook update procedure)")


def _migrate_status() -> tuple[list[str], list[str]]:
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
        raise CutoverError(f"migrate status could not connect: {state.get('error')}")
    return (
        [e["id"] for e in state.get("applied", [])],
        [e["id"] for e in state.get("pending", [])],
    )


def _env_value(text: str, key: str) -> str:
    """``key``'s value in dotenv-style text — lenient on comments,
    ``export``, quotes, and whitespace (CLAUDE.md, parsing)."""
    for line in text.splitlines():
        stripped = line.strip().removeprefix("export ").lstrip()
        if stripped.startswith("#"):
            continue
        name, sep, value = stripped.partition("=")
        if sep and name.strip() == key:
            return value.strip().strip("'\"")
    raise CutoverError(f"{key} is not set in the env file")


def _url_identity(url: str) -> tuple[str, str, str]:
    """``(host, port, dbname)``; loopback spellings collapse to one host so
    ``localhost`` and ``127.0.0.1`` compare equal, nothing looser."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "localhost"
    return host, str(parsed.port or 5432), parsed.path.lstrip("/")


def verify_migrate_target(checkout_env: str, production_env: str) -> str:
    """Refuse to migrate unless the checkout's maintenance URL targets the
    database the production units use (267 code review; sql.md: verify the
    target before destructive/DDL work). Returns the database name."""
    maintenance = _url_identity(
        _env_value(checkout_env, "MT_TIMESCALE_MAINTENANCE_URL")
    )
    application = _url_identity(_env_value(production_env, "MT_TIMESCALE_DB_URL"))
    if maintenance != application:
        raise CutoverError(
            "the checkout .env maintenance URL targets "
            f"{maintenance[0]}:{maintenance[1]}/{maintenance[2]} but the "
            f"production units use {application[0]}:{application[1]}/"
            f"{application[2]} — refusing to migrate a different database"
        )
    return maintenance[2]


def migrate(migration_id: str) -> None:
    """Apply the kalshi track from this checkout until ``migration_id`` is in."""
    dbname = verify_migrate_target(
        Path(".env").read_text(), out(["cat", str(ENV_FILE)], sudo=True)
    )
    print(f"    maintenance URL targets the production database ({dbname})")
    applied, pending = _migrate_status()
    if migration_id in applied:
        print(f"    {migration_id} already applied")
    elif migration_id in pending:
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
        if migration_id not in applied_now:
            raise CutoverError(f"apply did not report {migration_id}: {applied_now}")
        print(f"    applied {applied_now}")
    else:
        raise CutoverError(
            f"this checkout does not define {migration_id} (applied={applied}, "
            f"pending={pending}) — check out the release ref here first"
        )
    _, pending = _migrate_status()
    if pending:
        raise CutoverError(f"kalshi track still pending after apply: {pending}")
    print(f"    {MIGRATION_TRACK} track: 0 pending")


def journal_cursor(unit: str = PASS_UNIT) -> str:
    text = out(["journalctl", "-u", unit, "-n", "0", "--show-cursor", "-q"])
    match = re.search(r"-- cursor: (\S+)", text)
    if not match:
        raise CutoverError(f"could not read a journal cursor from: {text!r}")
    return match.group(1)


def fire() -> tuple[str, str]:
    """One supervised firing, streamed; returns the journal cursor taken
    before it and the start instant."""
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


def unit_result(unit: str = PASS_UNIT) -> tuple[str, str]:
    result = out(["systemctl", "show", unit, "-p", "Result", "--value"])
    status = out(["systemctl", "show", unit, "-p", "ExecMainStatus", "--value"])
    return result, status


def wait_for_unit_to_end(unit: str) -> None:
    """Block while ``unit`` is active. Sibling of ``wait_for_pass_to_end`` for
    the minute/daily acquisition units (slice 921)."""
    while unit_active(unit):
        print(f"    {unit} is running — waiting ({POLL_SECONDS}s) …", flush=True)
        time.sleep(POLL_SECONDS)


def journal_line_since(
    unit: str, cursor: str, pattern: re.Pattern[str]
) -> re.Match[str] | None:
    """First match of ``pattern`` in ``unit``'s journal after ``cursor``."""
    text = out(
        [
            "journalctl",
            "-u",
            unit,
            "--after-cursor",
            cursor,
            "-q",
            "--no-pager",
            "-o",
            "cat",
        ]
    )
    return pattern.search(text)


def wait_for_unit_or_line(unit: str, cursor: str, pattern: re.Pattern[str]) -> bool:
    """Block until ``pattern`` appears in the journal or ``unit`` ends.

    Returns True when the line was seen (the unit may still be running),
    False when the unit ended without it. Slice 921 / #22: a minute firing is
    unbounded — after its trailing phase it backfills until quota runs out —
    so a cutover that waits for the unit to END waits for days. It waits for
    the line it needs instead.
    """
    while unit_active(unit):
        if journal_line_since(unit, cursor, pattern) is not None:
            return True
        print(f"    {unit} is running — waiting ({POLL_SECONDS}s) …", flush=True)
        time.sleep(POLL_SECONDS)
    return journal_line_since(unit, cursor, pattern) is not None


def stop_unit(unit: str) -> None:
    """``systemctl stop`` and wait for the unit to be gone."""
    run(["systemctl", "stop", unit], sudo=True)
    wait_for_unit_to_end(unit)


def fire_unit_until(
    unit: str, mt_run_args: list[str], pattern: re.Pattern[str]
) -> tuple[str, str, bool]:
    """Fire ``unit`` and stop it once ``pattern`` shows in its journal.

    Returns (journal cursor, start instant, pattern seen). The unit is left
    alone when it ends before the line appears — the journal then explains
    why. Sibling of ``fire_unit``, which waits for the unit itself to end.
    """
    cursor = journal_cursor(unit)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    # NOT ``mt-run``: it streams the journal and returns only when the unit
    # ENDS, which for a backfill-bound minute firing is days — the 0.14.1
    # cutover sat inside it for two hours after the trailing line. Start the
    # unit without blocking and watch the journal for the line instead.
    print(f"    systemctl start --no-block {unit}; follow with: mt-run follow")
    run(["systemctl", "start", "--no-block", unit], sudo=True)
    reached = wait_for_unit_or_line(unit, cursor, pattern)
    if reached and unit_active(unit):
        say(f"  {pattern.pattern!r} reached — stopping {unit}; the timers own the rest")
        stop_unit(unit)
    return cursor, started, reached


def fire_unit(unit: str, mt_run_args: list[str]) -> tuple[str, str]:
    """One supervised firing of an arbitrary pass unit (slice 921).

    Sibling of ``fire`` — same cursor → run → wait shape, but for the minute
    and daily acquisition units rather than the Kalshi one. Returns the
    journal cursor taken before the firing and the start instant.
    """
    cursor = journal_cursor(unit)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    print(
        f"    sudo mt-run {' '.join(mt_run_args)} — streaming; Ctrl-C only "
        "detaches, the script keeps waiting"
    )
    run(["mt-run", *mt_run_args], sudo=True, check=False, stream=True)
    wait_for_unit_to_end(unit)
    print("    sudo -v — a password prompt here is normal after a long firing")
    run(["sudo", "-v"], stream=True)  # the firing may outlive the sudo grace period
    return cursor, started


def read_unit_journal(cursor: str, unit: str) -> "Firing":
    """``read_journal`` for an arbitrary unit (slice 921)."""
    return _read_journal_for(cursor, unit)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

Entry = tuple[datetime, str]


@dataclass
class Firing:
    """One firing's journal entries and the checks a script records on it."""

    entries: list[Entry]
    checks: list[tuple[bool, str]] = field(default_factory=list)

    def check(self, ok: bool, text: str) -> None:
        self.checks.append((ok, text))

    def find(self, needle: str) -> list[Entry]:
        return [(ts, m) for ts, m in self.entries if needle in m]

    def first(self, pattern: re.Pattern[str]) -> re.Match[str] | None:
        return next(
            (pattern.search(m) for _, m in self.entries if pattern.search(m)), None
        )

    @property
    def all_ok(self) -> bool:
        return all(ok for ok, _ in self.checks)


def read_journal(cursor: str) -> Firing:
    return _read_journal_for(cursor, PASS_UNIT)


def _read_journal_for(cursor: str, unit: str) -> Firing:
    raw = out(
        [
            "journalctl",
            "-u",
            unit,
            f"--after-cursor={cursor}",
            "-o",
            "json",
            "--no-pager",
            "-q",
        ]
    )
    entries: list[Entry] = []
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
