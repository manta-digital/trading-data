"""Host reads and writes for the slice 234 cutover.

Everything here touches manta9000: the archive directory, the database
clusters (read-only figures), the slice cgroup, ``mt-serve``'s health route.
Database URLs are read from the env files and never printed.
"""

from __future__ import annotations

import grp
import inspect
import json
import os
import stat
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import psycopg
from cutover_234_checks import ClusterLoad, memory_max_bytes, oom_kills
from cutover_common import out, unit_active

GROUP = "manta-trading"
SYSTEMD_DIR = Path("/etc/systemd/system")
SAMPLE_SECONDS = 2
HEALTH_WAIT_SECONDS = 60


# -- Archive directory (TD4 step 2) ------------------------------------------------


def archive_entries(root: Path) -> list[Path]:
    paths = [root]
    for dirpath, dirnames, filenames in os.walk(root):
        paths += [Path(dirpath) / n for n in (*dirnames, *filenames)]
    return paths


def archive_problems(root: Path) -> list[str]:
    """Entries outside the group, not group-writable, or a directory without
    setgid. Empty means the archive is shared correctly."""
    gid = grp.getgrnam(GROUP).gr_gid
    problems = []
    for path in archive_entries(root):
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            continue
        if st.st_gid != gid or not st.st_mode & stat.S_IWGRP:
            problems.append(str(path))
        elif stat.S_ISDIR(st.st_mode) and not st.st_mode & stat.S_ISGID:
            problems.append(str(path))
    return problems


def share_archive(root: Path) -> None:
    """``chgrp -R``, ``chmod -R g+w`` and ``g+s`` on directories, as the owner."""
    gid = grp.getgrnam(GROUP).gr_gid
    for path in archive_entries(root):
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            continue
        os.chown(path, -1, gid)
        mode = st.st_mode | stat.S_IWGRP
        if stat.S_ISDIR(st.st_mode):
            mode |= stat.S_ISGID
        os.chmod(path, stat.S_IMODE(mode))


# -- Databases (TD4 steps 3, 6) ------------------------------------------------------


def pass_check_definition(url: str) -> str:
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'pass_runs_pass_check'"
        ).fetchone()
    return "" if row is None else str(row[0])


def cluster_load(url: str) -> ClusterLoad:
    with psycopg.connect(url) as conn:
        max_conn = conn.execute("SHOW max_connections").fetchone()
        reserved = conn.execute("SHOW superuser_reserved_connections").fetchone()
        live = conn.execute("SELECT count(*) FROM pg_stat_activity").fetchone()
    assert max_conn and reserved and live
    return ClusterLoad(int(max_conn[0]), int(reserved[0]), int(live[0]))


# -- mt-serve (TD4 step 5) -------------------------------------------------------------


def serve_port() -> int:
    """``mt serve``'s default port, from the command's own signature: the
    installed unit passes no ``--port``."""
    from manta_trading.cli.commands.serve import serve

    return int(inspect.signature(serve).parameters["port"].default.default)


def api_health() -> dict[str, object]:
    """``/api/v1/health``, retried while the restarted server comes up."""
    url = f"http://127.0.0.1:{serve_port()}/api/v1/health"
    deadline = time.monotonic() + HEALTH_WAIT_SECONDS
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return dict(json.loads(response.read()))
        except OSError:
            # The server is still starting; give up only at the deadline.
            if time.monotonic() > deadline:
                raise
            time.sleep(SAMPLE_SECONDS)


# -- Slice cgroup (TD4 step 8, issue #33) --------------------------------------------


def slice_cgroup() -> Path:
    group = out(
        [
            "systemctl",
            "show",
            "manta-acquisition.slice",
            "-p",
            "ControlGroup",
            "--value",
        ]
    )
    return Path("/sys/fs/cgroup") / group.lstrip("/")


def installed_memory_max() -> int:
    return memory_max_bytes((SYSTEMD_DIR / "manta-acquisition.slice").read_text())


class CgroupSampler:
    """Reads the slice's ``memory.max`` and ``oom_kill`` while ``unit`` runs.

    Started before the firing, on its own thread, because ``fire_unit``
    blocks until the unit ends and the slice cgroup is only guaranteed to
    exist while a member is active.
    """

    def __init__(self, unit: str, read: Callable[[Path], str] = Path.read_text):
        self._unit = unit
        self._read = read
        self.memory_max: str | None = None
        self.oom_kill: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        cgroup = slice_cgroup()
        if (cgroup / "memory.events").exists():
            self.oom_kill = oom_kills(self._read(cgroup / "memory.events"))

    def _loop(self) -> None:
        while not self._stop.wait(SAMPLE_SECONDS):
            if not unit_active(self._unit):
                continue
            cgroup = slice_cgroup()
            self.memory_max = self._read(cgroup / "memory.max").strip()
            self.oom_kill = oom_kills(self._read(cgroup / "memory.events"))
