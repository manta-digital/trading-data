"""``workers``: whole-set ingest at 1, 2 and 4 workers (LLD 226 TD3).

Rule: keep 2 unless 4 is at least 1.5× faster *and* the contention run
(Section 7) stays within its bound; so this report's verdict is provisional
until contention is measured. Also records the tick cluster's peak resident
memory per run (the go/no-go reads it against the memory settings).
"""

from __future__ import annotations

from pathlib import Path

from manta_trading.data.tick.constants import TICK_INGEST_WORKERS
from proof_226.common import (
    ProofSetupError,
    Report,
    load_proof_urls,
    require_free_space,
)
from proof_226.host import (
    PeakSampler,
    cgroup_rss_bytes,
    gib,
    meminfo_bytes,
    unit_cgroup,
)
from proof_226.ingest_runs import IngestRun, ingest, patched, reload_whole_set, reset

WORKER_COUNTS = (1, 2, 4)
#: 4 workers must beat the current count's wall time by this factor.
SPEEDUP_TO_CHANGE = 1.5
#: Do not start a whole-set reload with less than this available.
MEM_AVAILABLE_FLOOR = 16 * 1024**3


def verdict(seconds: dict[int, float], current: int = TICK_INGEST_WORKERS) -> str:
    """TD3's rule over measured wall times, stated provisionally."""
    best = max(WORKER_COUNTS)
    speedup = seconds[current] / seconds[best]
    if speedup >= SPEEDUP_TO_CHANGE:
        return (
            f"{best} workers are {speedup:.2f}× faster than {current} "
            f"(≥ {SPEEDUP_TO_CHANGE}×): change to {best} **if** the contention run "
            "stays within its bound (provisional until Section 7)."
        )
    return (
        f"{best} workers are {speedup:.2f}× faster than {current} "
        f"(< {SPEEDUP_TO_CHANGE}×): keep {current} (provisional until Section 7)."
    )


def run() -> Path:
    urls = load_proof_urls()
    require_free_space()
    available = meminfo_bytes("MemAvailable")
    if available < MEM_AVAILABLE_FLOOR:
        raise ProofSetupError(
            f"MemAvailable {gib(available)} is below {gib(MEM_AVAILABLE_FLOOR)}"
        )
    cgroup = unit_cgroup()
    report = Report("workers", "Proof: ingest workers 1, 2, 4 (slice 226)")
    report.add(
        f"MemAvailable at start: {gib(available)}; "
        f"tick cluster RSS at start: {gib(cgroup_rss_bytes(cgroup))}",
        "",
    )
    runs: dict[int, IngestRun] = {}
    rows = []
    for count in WORKER_COUNTS:
        reset(urls)
        with patched("TICK_INGEST_WORKERS", count), PeakSampler(cgroup) as peak:
            runs[count] = ingest(urls)
        r = runs[count]
        rows.append(
            (
                count,
                f"{r.seconds:.1f}",
                f"{r.decode_seconds:.1f}",
                f"{r.write_seconds:.1f}",
                f"{peak.peak_cpu_pct:.0f} %",
                gib(peak.peak_cluster_rss),
                r.ingested,
                r.failed,
            )
        )
    report.table(
        (
            "workers",
            "wall s",
            "Σ decode s",
            "Σ write s",
            "peak host CPU",
            "peak tick cluster RSS",
            "units",
            "failed",
        ),
        rows,
    )
    report.add(
        "Cluster RSS is the summed VmRSS of the cluster's processes (shared "
        "buffers counted once per process that touched them: an upper bound).",
        "",
        f"**Verdict:** {verdict({c: r.seconds for c, r in runs.items()})}",
        "",
        f"Proof database reloaded at {TICK_INGEST_WORKERS} workers: "
        f"{reload_whole_set(urls):,} rows.",
    )
    return report.write()
