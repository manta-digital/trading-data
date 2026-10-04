"""Host readings for the proof harness: CPU, memory, the tick cluster's RSS.

All from ``/proc`` and the cgroup tree, read-only and without root. The tick
cluster's processes are found through its systemd unit's cgroup.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

logger = logging.getLogger(__name__)

TICK_UNIT = "postgresql@17-tick.service"
CGROUP_ROOT = Path("/sys/fs/cgroup")
SAMPLE_SECONDS = 1.0
_KIB = 1024


def cpu_times(stat: Path = Path("/proc/stat")) -> tuple[int, int, int]:
    """``(busy, iowait, total)`` jiffies from the aggregate ``cpu`` line."""
    fields = [int(v) for v in stat.read_text().split("\n", 1)[0].split()[1:]]
    idle, iowait = fields[3], fields[4]
    total = sum(fields[:8])  # guest time is already inside user/nice
    return total - idle - iowait, iowait, total


def meminfo_bytes(key: str, meminfo: Path = Path("/proc/meminfo")) -> int:
    for line in meminfo.read_text().splitlines():
        name, _, rest = line.partition(":")
        if name == key:
            return int(rest.split()[0]) * _KIB
    raise KeyError(f"{key} not in {meminfo}")


def unit_cgroup(unit: str = TICK_UNIT) -> Path:
    out = subprocess.run(
        ["systemctl", "show", "-P", "ControlGroup", unit],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not out:
        raise RuntimeError(f"{unit} has no control group (is it running?)")
    return CGROUP_ROOT / out.lstrip("/")


def cgroup_rss_bytes(cgroup: Path) -> int:
    """Summed ``VmRSS`` of the cgroup's processes (shared pages counted per
    process, so an upper bound on the cluster's resident memory)."""
    total = 0
    for pid in (cgroup / "cgroup.procs").read_text().split():
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except FileNotFoundError:  # a backend that exited between the reads
            continue
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                total += int(line.split()[1]) * _KIB
    return total


def reset_own_peak_rss() -> int:
    """Reset this process's ``VmHWM`` (``clear_refs`` 5); return current RSS."""
    Path("/proc/self/clear_refs").write_text("5")
    return own_status_bytes("VmRSS")


def own_status_bytes(key: str) -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(f"{key}:"):
            return int(line.split()[1]) * _KIB
    raise KeyError(key)


@dataclass
class PeakSampler:
    """Samples host CPU busy % and the tick cluster's RSS until closed."""

    cgroup: Path
    interval: float = SAMPLE_SECONDS
    peak_cpu_pct: float = 0.0
    peak_cluster_rss: int = 0
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    #: Set by a sampler that died; ``__exit__`` raises it after ``join``.
    _error: Exception | None = None

    def __enter__(self) -> PeakSampler:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        assert self._thread is not None
        self._thread.join()
        if self._error is not None and exc is None:
            raise RuntimeError("peak sampler died; peaks are understated") from (
                self._error
            )

    def _loop(self) -> None:
        try:
            self._sample()
        except Exception as exc:  # thread boundary: record it for __exit__
            logger.exception("peak sampler died")
            self._error = exc

    def _sample(self) -> None:
        busy0, _, total0 = cpu_times()
        while not self._stop.wait(self.interval):
            busy, _, total = cpu_times()
            if total > total0:
                pct = 100 * (busy - busy0) / (total - total0)
                self.peak_cpu_pct = max(self.peak_cpu_pct, pct)
            busy0, total0 = busy, total
            self.peak_cluster_rss = max(
                self.peak_cluster_rss, cgroup_rss_bytes(self.cgroup)
            )


def gib(value: int) -> str:
    return f"{value / 1024**3:.2f} GiB"


def mib(value: int) -> str:
    return f"{value / 1024**2:.0f} MiB"
