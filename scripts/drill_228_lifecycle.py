"""The slice 228 restore drill's lifecycle: bounds, paths, lock, marker, cleanup.

Everything the drill creates lives in one marked directory,
``/data/restore-test/228-drill-<stamp>/`` (TD5). The marker file is written
first, so any directory without it was not made by this drill and is never
touched. Destructive actions (``pg_ctl stop``, ``sudo -n rm -rf``) target only
a marked drill directory, checked after resolving symlinks.

Host commands are passed in as a ``Runner`` (``drill_228_host`` supplies the
real ones), so this module holds the rules and the tests stub the host.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import IO

# --- TD6 bounds (the one definition) -------------------------------------------

#: Statement timeout on production reads (steps 1, 2, 5).
PROD_STATEMENT_TIMEOUT = timedelta(minutes=10)
#: Statement timeout on scratch-server reads (steps 5, 7).
SCRATCH_STATEMENT_TIMEOUT = timedelta(minutes=10)
#: Wait for step 2's WAL segment to reach the archive.
WAL_WAIT = timedelta(seconds=120)
#: restic restore (step 3); also bounds step 1's `restic ls`.
RESTIC_TIMEOUT = timedelta(minutes=30)
BASE_EXTRACT_TIMEOUT = timedelta(minutes=30)
VERIFYBACKUP_TIMEOUT = timedelta(minutes=30)
#: Wait for the scratch server to leave recovery.
RECOVERY_TIMEOUT = timedelta(minutes=15)
#: Each ``mt data`` rebuild command (step 6).
MT_COMMAND_TIMEOUT = timedelta(minutes=60)
#: ``pg_ctl stop -m fast -t <this>`` (step 8 and the leftover sweep).
PG_STOP_TIMEOUT = timedelta(seconds=60)

# --- Paths --------------------------------------------------------------------

DRILL_ROOT = Path("/data/restore-test")
DIR_PREFIX = "228-drill-"
MARKER_NAME = "drill-228.json"
LOCK_PATH = DRILL_ROOT / ".228-drill.lock"
#: PostgreSQL 17's binaries (``pg_ctl``, ``pg_verifybackup``: no /usr/bin wrapper).
PG_BIN = Path("/usr/lib/postgresql/17/bin")
#: Where the report goes, relative to the checkout root.
REPORT_DIR = Path("project-documents/user/notes")
#: Inside a drill directory: the scratch server's data and socket directories,
#: and restic's restore target.
PGDATA_NAME = "pgdata"
SOCKET_NAME = "sock"
RESTORE_NAME = "restic-restore"

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class DrillError(RuntimeError):
    """The drill refuses to act; the message names what it saw."""


# --- Lock and marker ------------------------------------------------------------


@dataclass
class DrillLock:
    """The drill's ``flock``, held for the whole run (TD1 step 0)."""

    path: Path
    handle: IO[str] | None = None

    @property
    def held(self) -> bool:
        return self.handle is not None

    def release(self) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def acquire_drill_lock(path: Path = LOCK_PATH) -> DrillLock:
    """Take the drill lock or refuse, naming the holder's pid."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        holder = handle.read().strip() or "unknown pid"
        handle.close()
        raise DrillError(f"another drill holds {path} ({holder})") from None
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid {os.getpid()}\n")
    handle.flush()
    return DrillLock(path, handle)


def create_drill_dir(stamp: str, root: Path = DRILL_ROOT) -> Path:
    """Make ``<root>/228-drill-<stamp>`` and write its marker before anything else."""
    root.mkdir(parents=True, exist_ok=True)
    drill = root / f"{DIR_PREFIX}{stamp}"
    drill.mkdir(mode=0o700)
    (drill / MARKER_NAME).write_text(json.dumps({"stamp": stamp, "pid": os.getpid()}))
    return drill


# --- Path checks and cleanup -------------------------------------------------------


def is_drill_dir(path: Path, root: Path = DRILL_ROOT) -> bool:
    """A direct child of the root, with the drill prefix and the marker."""
    return (
        path.parent == root.resolve()
        and path.name.startswith(DIR_PREFIX)
        and (path / MARKER_NAME).is_file()
    )


def assert_drill_path(path: Path, root: Path = DRILL_ROOT) -> Path:
    """The resolved path, if it is a marked drill directory or lies inside one."""
    resolved, base = path.resolve(), root.resolve()
    if not resolved.is_relative_to(base) or resolved == base:
        raise DrillError(f"{path} (resolved {resolved}) is not under {base}")
    drill = base / resolved.relative_to(base).parts[0]
    if not is_drill_dir(drill, root):
        raise DrillError(f"{drill} is not a marked {DIR_PREFIX}* directory")
    return resolved


def remove_drill_dir(path: Path, sudo_n: Runner, root: Path = DRILL_ROOT) -> None:
    """``sudo -n rm -rf`` a drill directory itself (never a path inside one)."""
    resolved = assert_drill_path(path, root)
    if not is_drill_dir(resolved, root):
        raise DrillError(
            f"{resolved} is inside a drill directory, not one; not removed"
        )
    result = sudo_n(["rm", "-rf", "--", str(resolved)])
    if result.returncode != 0 or resolved.exists():
        raise DrillError(
            f"could not remove {resolved} (exit {result.returncode}: "
            f"{result.stderr.strip()}); it is left in place"
        )


def pg_ctl(*args: str) -> list[str]:
    return [str(PG_BIN / "pg_ctl"), *args]


def stop_scratch_server(drill: Path, run: Runner, root: Path = DRILL_ROOT) -> bool:
    """Stop the scratch server running from ``drill``; ``False`` if none runs."""
    pgdata = assert_drill_path(drill, root) / PGDATA_NAME
    if not pgdata.is_dir() or run(pg_ctl("status", "-D", str(pgdata))).returncode != 0:
        return False
    seconds = str(int(PG_STOP_TIMEOUT.total_seconds()))
    result = run(pg_ctl("stop", "-D", str(pgdata), "-m", "fast", "-t", seconds))
    if result.returncode != 0:
        raise DrillError(f"pg_ctl stop on {pgdata} exited {result.returncode}")
    return True


def sweep_leftovers(
    lock: DrillLock, run: Runner, sudo_n: Runner, root: Path = DRILL_ROOT
) -> list[Path]:
    """TD5: remove every marked leftover drill directory; refuse an unmarked one."""
    if not lock.held:
        raise DrillError("the leftover sweep runs only under the drill lock")
    if not root.exists():
        return []
    removed: list[Path] = []
    for path in sorted(root.glob(f"{DIR_PREFIX}*")):
        if not is_drill_dir(path.resolve(), root):
            raise DrillError(f"{path} has no {MARKER_NAME}; not made by this drill")
        stop_scratch_server(path, run, root)
        remove_drill_dir(path, sudo_n, root)
        removed.append(path)
    return removed
