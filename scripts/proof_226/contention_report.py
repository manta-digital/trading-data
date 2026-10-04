"""The contention report and verdict (LLD 226 TD7).

Each overlapped Kalshi duration against the week's distribution; every
sampled series as median and 95th percentile, overlapped against solo; the
tick ingest rate with Kalshi running against the loop's rate without it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import partial
from statistics import median, quantiles

from manta_trading.data.tick.constants import TICK_INGEST_WORKERS
from proof_226.common import Report
from proof_226.contention_run import KALSHI_WEEK_MAX_SECONDS, Firing, Outcome
from proof_226.contention_sampler import DEVICES, Sample

#: A pass_runs row matches an observed firing when it started this close to it.
MATCH_SECONDS = 120
NOT_A_CLEARANCE = (
    "This is not a clearance for the minute pass: Kalshi writes little and "
    "often, the minute pass walks the whole equity universe, and 233 measures "
    "that overlap."
)

_MIB = 1024 * 1024


def _disk_read(sample: Sample, device: str) -> float:
    return sample.disk_read_bps.get(device, 0.0) / _MIB


def _disk_write(sample: Sample, device: str) -> float:
    return sample.disk_write_bps.get(device, 0.0) / _MIB


#: Series reported: label → value of one sample.
SERIES: dict[str, Callable[[Sample], float]] = {
    "CPU busy %": lambda s: s.cpu_busy_pct,
    "iowait %": lambda s: s.iowait_pct,
    "MemAvailable GiB": lambda s: s.mem_available / 1024**3,
    "Committed_AS / CommitLimit %": lambda s: 100 * s.committed_as / s.commit_limit,
    "production commits/s": lambda s: s.prod_commits_ps,
    "production tuples/s": lambda s: s.prod_tuples_ps,
    "tick rows/s": lambda s: s.tick_rows_ps,
    **{f"{d} read MiB/s": partial(_disk_read, device=d) for d in DEVICES},
    **{f"{d} write MiB/s": partial(_disk_write, device=d) for d in DEVICES},
}


def verdict(
    overlapped_seconds: list[float],
    loop_failures: list[str],
    workers: int = TICK_INGEST_WORKERS,
) -> str:
    if loop_failures:
        return (
            f"none: the tick loop failed {len(loop_failures)} time(s), so the "
            "overlapped firings did not run under a known load"
        )
    if any(s > KALSHI_WEEK_MAX_SECONDS for s in overlapped_seconds):
        return (
            "measurable contention: an overlapped Kalshi pass ran over the "
            f"week's maximum of {KALSHI_WEEK_MAX_SECONDS:.0f} s"
        )
    return f"none measured at {workers} ingest workers. {NOT_A_CLEARANCE}"


def summary(values: list[float]) -> str:
    """``median / p95`` (p95 is the max with fewer than two values)."""
    if not values:
        return "n/a"
    if len(values) < 2:
        return f"{values[0]:.1f} / {values[0]:.1f}"
    return f"{median(values):.1f} / {quantiles(values, n=20)[-1]:.1f}"


def firing_seconds(firing: Firing, runs: list[tuple[datetime, float]]) -> float | None:
    for started, seconds in runs:
        if abs((started - firing.started).total_seconds()) <= MATCH_SECONDS:
            return seconds
    return None


def _windows(outcome: Outcome) -> dict[str, list[Sample]]:
    """Samples while Kalshi ran, split by whether the tick loop also ran; and
    the tick loop's samples with Kalshi idle."""
    out: dict[str, list[Sample]] = {"overlapped": [], "solo": [], "tick alone": []}
    for _, sample, loaded, active in outcome.samples:
        if active:
            out["overlapped" if loaded else "solo"].append(sample)
        elif loaded:
            out["tick alone"].append(sample)
    return out


def write_report(
    report: Report,
    outcome: Outcome,
    week: list[tuple[datetime, float]],
    runs: list[tuple[datetime, float]],
    iterations: int,
    loop_failures: list[str],
) -> None:
    durations = sorted(seconds for _, seconds in week)
    report.add(
        "## The week before (pass_runs, kalshi)",
        "",
        f"{len(durations)} runs; median / p95 {summary(durations)} s; "
        f"min {durations[0] if durations else 'n/a'} s, "
        f"max {durations[-1] if durations else 'n/a'} s",
        "",
    )
    rows, overlapped = [], []
    for firing in outcome.firings:
        seconds = firing_seconds(firing, runs)
        if firing.overlapped and seconds is not None:
            overlapped.append(seconds)
        rows.append(
            (
                "overlapped" if firing.overlapped else "solo",
                f"{firing.started:%H:%M:%S}",
                "n/a" if seconds is None else f"{seconds:.0f}",
            )
        )
    report.add("## Kalshi firings", "")
    report.table(("firing", "started (UTC)", "duration s"), rows)
    windows = _windows(outcome)
    report.add(
        f"## Series (median / p95; {len(outcome.samples)} samples, "
        f"{iterations} tick-loop iterations)",
        "",
    )
    report.table(
        ("series", *windows),
        [
            (label, *(summary([f(s) for s in w]) for w in windows.values()))
            for label, f in SERIES.items()
        ],
    )
    guards = outcome.lines + [f"TICK LOOP: {f}" for f in loop_failures]
    report.add("## Guards", "", *(guards or ["No guard tripped."]), "")
    report.add(f"**Verdict:** {verdict(overlapped, loop_failures)}")
