"""One contention sample every 5 s (LLD 226 TD7).

Host CPU busy and iowait (``/proc/stat``); ``MemAvailable``, ``Committed_AS``
and ``CommitLimit`` (``/proc/meminfo``); bytes read and written per device
(``/proc/diskstats``); ``/data`` free space; the production cluster's commit
and tuple counters (``pg_stat_database``, read-only, 5 s statement timeout);
the tick loop's rows per second (the proof database's ``tup_inserted``).
Counters become rates over the interval between two readings.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from proof_226.common import DATA_VOLUME
from proof_226.host import cpu_times, meminfo_bytes

#: nvme0n1 carries the production cluster; nvme1n1 carries /data and the tick
#: cluster (TD7).
DEVICES = ("nvme0n1", "nvme1n1")
SECTOR_BYTES = 512
PRODUCTION_TIMEOUT_MS = 5000
_PROD_COUNTERS = (
    "SELECT sum(xact_commit), sum(tup_inserted + tup_updated + tup_deleted)"
    " FROM pg_stat_database"
)
_TICK_INSERTS = (
    "SELECT tup_inserted FROM pg_stat_database WHERE datname = current_database()"
)


@dataclass(frozen=True)
class Reading:
    """Raw counters at one instant."""

    at: float
    cpu: tuple[int, int, int]
    disk: dict[str, tuple[int, int]]
    prod: tuple[int, int]
    tick_inserted: int
    mem_available: int
    committed_as: int
    commit_limit: int
    data_free: int


@dataclass(frozen=True)
class Sample:
    """Rates over one interval, and the gauges at its end."""

    at: float
    cpu_busy_pct: float
    iowait_pct: float
    mem_available: int
    committed_as: int
    commit_limit: int
    data_free: int
    disk_read_bps: dict[str, float]
    disk_write_bps: dict[str, float]
    prod_commits_ps: float
    prod_tuples_ps: float
    tick_rows_ps: float


def diskstats(path: Path = Path("/proc/diskstats")) -> dict[str, tuple[int, int]]:
    """``device → (sectors read, sectors written)`` for ``DEVICES``."""
    out = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if fields[2] in DEVICES:
            out[fields[2]] = (int(fields[5]), int(fields[9]))
    return out


def production_connection(url: str) -> psycopg.Connection[Any]:
    """Read-only, 5 s statement timeout, autocommit (TD7)."""
    return psycopg.connect(
        url,
        autocommit=True,
        options=(
            "-c default_transaction_read_only=on"
            f" -c statement_timeout={PRODUCTION_TIMEOUT_MS}"
        ),
    )


def read(
    at: float, prod: psycopg.Connection[Any], tick: psycopg.Connection[Any]
) -> Reading:
    commits, tuples = prod.execute(_PROD_COUNTERS).fetchone() or (0, 0)
    (inserted,) = tick.execute(_TICK_INSERTS).fetchone() or (0,)
    return Reading(
        at=at,
        cpu=cpu_times(),
        disk=diskstats(),
        prod=(int(commits), int(tuples)),
        tick_inserted=int(inserted),
        mem_available=meminfo_bytes("MemAvailable"),
        committed_as=meminfo_bytes("Committed_AS"),
        commit_limit=meminfo_bytes("CommitLimit"),
        data_free=shutil.disk_usage(DATA_VOLUME).free,
    )


def rates(before: Reading, after: Reading) -> Sample:
    seconds = max(after.at - before.at, 1e-9)
    busy = after.cpu[0] - before.cpu[0]
    iowait = after.cpu[1] - before.cpu[1]
    total = max(after.cpu[2] - before.cpu[2], 1)

    def per_second(new: int, old: int) -> float:
        return (new - old) / seconds

    return Sample(
        at=after.at,
        cpu_busy_pct=100 * busy / total,
        iowait_pct=100 * iowait / total,
        mem_available=after.mem_available,
        committed_as=after.committed_as,
        commit_limit=after.commit_limit,
        data_free=after.data_free,
        disk_read_bps={
            d: per_second(after.disk[d][0], before.disk[d][0]) * SECTOR_BYTES
            for d in after.disk
        },
        disk_write_bps={
            d: per_second(after.disk[d][1], before.disk[d][1]) * SECTOR_BYTES
            for d in after.disk
        },
        prod_commits_ps=per_second(after.prod[0], before.prod[0]),
        prod_tuples_ps=per_second(after.prod[1], before.prod[1]),
        tick_rows_ps=per_second(after.tick_inserted, before.tick_inserted),
    )
