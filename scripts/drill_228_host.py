"""Host primitives for the slice 228 restore drill: sudo, restic, the archive set.

``prime_sudo`` asks for the one password up front (TD1 step 0). Every later
root call goes through ``sudo_n``, so an expired timestamp fails by name
instead of waiting on a prompt. 227's ``cutover_common.run`` and
``cutover_227_host.psql`` use plain ``sudo`` and are left as they are (the
1.1 decision).

The archive comparisons (step 1's archive-vs-snapshot check, step 3's
restored-archive check) use one definition of the archive's backed-up set:
its files minus the patterns ``deploy/restic-excludes.txt`` excludes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from drill_228_lifecycle import DrillError

CHECKOUT = Path(__file__).resolve().parents[1]
EXCLUDE_FILE = CHECKOUT / "deploy" / "restic-excludes.txt"
RESTIC_REPO = CHECKOUT / "deploy" / "lib" / "restic_repo.sh"
#: The restic repository prefix of the nightly system backup (runbook 200).
RESTIC_PREFIX = "system"
_SUDO_PROMPT_NEEDED = "a password is required"


class SudoExpired(DrillError):
    """``sudo -n`` needed a password: the step-0 timestamp ran out."""


# --- sudo ---------------------------------------------------------------------


def prime_sudo() -> None:
    """``sudo -v``: the run's one password prompt, before any production lock."""
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        raise DrillError("sudo -v failed; the drill needs sudo for restic, chown, rm")


def run(
    args: list[str], timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """A command as manta, output captured, exit status left to the caller."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def sudo_n(
    args: list[str], timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """``sudo -n <args>``; a needed password raises ``SudoExpired`` by name."""
    result = run(["sudo", "-n", *args], timeout)
    if result.returncode != 0 and _SUDO_PROMPT_NEEDED in result.stderr:
        raise SudoExpired(
            f"sudo timestamp expired before: {' '.join(args[:3])}; re-run the drill"
        )
    return result


def postgres_value(cluster: str, sql: str) -> str:
    """One value from a cluster, as postgres over its local socket (``sudo -n``)."""
    args = ["-u", "postgres", "env", f"PGCLUSTER={cluster}", "psql", "-X", "-At"]
    result = sudo_n([*args, "-d", "postgres", "-c", sql])
    if result.returncode != 0:
        raise DrillError(
            f"psql on {cluster} exited {result.returncode}: {result.stderr}"
        )
    return result.stdout.strip()


def restic(env_file: Path, args: list[str], timeout: float) -> str:
    """A restic command through the repository wrapper, as root; stdout."""
    wrapper = [str(RESTIC_REPO), "--env-file", str(env_file), "--prefix", RESTIC_PREFIX]
    result = sudo_n([*wrapper, "run", "--", *args], timeout)
    if result.returncode != 0:
        raise DrillError(
            f"restic {args[0]} exited {result.returncode}: {result.stderr}"
        )
    return result.stdout


# --- The archive's backed-up set ------------------------------------------------


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """A restic exclude pattern as a regex on an absolute path.

    ``**`` spans any number of directories (zero included), ``*`` and ``?``
    stay within one path component, and a pattern without a leading ``/``
    matches at any depth.
    """
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out if pattern.startswith("/") else f"(?:.*/)?{out}")


def load_excludes(path: Path = EXCLUDE_FILE) -> list[re.Pattern[str]]:
    lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return [_pattern_regex(p) for p in lines if p]


def is_excluded(path: str, excludes: list[re.Pattern[str]]) -> bool:
    """A path is excluded when it or any parent directory matches."""
    parts = path.rstrip("/").split("/")
    candidates = ["/".join(parts[:n]) for n in range(2, len(parts) + 1)]
    return any(rx.fullmatch(c) for rx in excludes for c in candidates)


@dataclass(frozen=True)
class ArchiveSet:
    count: int
    bytes: int
    newest: datetime | None

    def describe(self) -> str:
        newest = self.newest.isoformat() if self.newest else "none"
        return f"{self.count} files, {self.bytes} bytes, newest {newest}"


def backed_up_set(
    archive: Path, excludes: list[re.Pattern[str]], logical_root: Path | None = None
) -> ArchiveSet:
    """The files under ``archive`` restic would back up. ``logical_root`` is the
    live path a restored copy stands for, so excludes match as they did live."""
    count = size = 0
    newest = 0.0
    for directory, _, files in os.walk(archive):
        for name in files:
            path = Path(directory, name)
            logical = (logical_root or archive) / path.relative_to(archive)
            if is_excluded(str(logical), excludes):
                continue
            stat = path.lstat()
            count, size, newest = (
                count + 1,
                size + stat.st_size,
                max(newest, stat.st_mtime),
            )
    return ArchiveSet(
        count, size, datetime.fromtimestamp(newest, UTC) if count else None
    )


def _parse_time(text: str) -> datetime:
    """restic's RFC 3339 time with up to nanoseconds, to an aware datetime."""
    match = re.fullmatch(
        r"(.*?\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", text.strip()
    )
    if match is None:
        raise DrillError(f"unreadable restic time {text!r}")
    base, fraction, zone = match.groups()
    micro = (fraction or "0")[:6].ljust(6, "0")
    return datetime.fromisoformat(f"{base}.{micro}{'+00:00' if zone == 'Z' else zone}")


@dataclass(frozen=True)
class SnapshotListing:
    time: datetime
    files: ArchiveSet


def parse_restic_ls(text: str, excludes: list[re.Pattern[str]]) -> SnapshotListing:
    """``restic ls --json``: one snapshot object, then one node per entry.

    Lenient: blank lines, surrounding whitespace and non-JSON lines (warnings)
    are skipped; a listing with no snapshot object is an error.
    """
    snapshot: dict[str, Any] | None = None
    count = size = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        item = json.loads(line)
        if item.get("struct_type") == "snapshot" or (
            "time" in item and "paths" in item
        ):
            snapshot = item
        elif item.get("type") == "file" and not is_excluded(item["path"], excludes):
            count, size = count + 1, size + int(item.get("size", 0))
    if snapshot is None:
        raise DrillError("restic ls printed no snapshot line")
    taken = _parse_time(snapshot["time"])
    return SnapshotListing(taken, ArchiveSet(count, size, None))


def check_archive_against_snapshot(live: ArchiveSet, snapshot: SnapshotListing) -> None:
    """TD1 step 1: refuse unless the latest snapshot holds the live archive."""
    problems = []
    if live.count != snapshot.files.count:
        problems.append(f"files: live {live.count}, snapshot {snapshot.files.count}")
    if live.bytes != snapshot.files.bytes:
        problems.append(f"bytes: live {live.bytes}, snapshot {snapshot.files.bytes}")
    if live.newest is not None and live.newest > snapshot.time:
        problems.append(f"newest live file {live.newest.isoformat()}")
    if problems:
        raise DrillError(
            f"archive changed since snapshot {snapshot.time.isoformat()}; run the "
            f"restic backup first ({'; '.join(problems)})"
        )
