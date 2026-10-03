"""``batch``: the three largest units at 8, 32 and 128 MiB budgets (TD3).

Rule: keep 32 MiB unless a worker's memory exceeds 4× the budget, or another
budget moves the units' time by more than 10 %. Workers are threads of this
process, so each run's rise is this process's high-water mark (reset before
the run) above its resident memory at the start; ``budget_memory`` separates
the part that scales with the budget from the fixed part.
Ends by reloading the whole set with the current constants, so Section 6
measures every row.
"""

from __future__ import annotations

from math import ceil
from pathlib import Path

import psycopg

from manta_trading.data.tick.constants import (
    STORED_TIERS,
    TICK_DECODE_BATCH_BYTES,
    TICK_INGEST_WORKERS,
)
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from proof_226.common import (
    Report,
    archive_root,
    load_proof_urls,
    require_free_space,
)
from proof_226.host import mib, own_status_bytes, reset_own_peak_rss
from proof_226.ingest_runs import IngestRun, ingest, patched, reload_whole_set, reset

_MIB = 1024 * 1024
BUDGETS = (8 * _MIB, 32 * _MIB, 128 * _MIB)
LARGEST_UNITS = 3
RSS_BUDGET_MULTIPLE = 4
TIME_CHANGE_FRACTION = 0.10


def largest_units(db_url: str, root: Path) -> list[tuple[int, int, int]]:
    """``(unit_id, records, record size)`` of the largest current tier units;
    the record size is read from each file (``DbnFile.record_size``)."""
    with psycopg.connect(db_url) as conn:
        rows = conn.execute(
            """
            SELECT unit.unit_id, unit.decoded_record_count, unit.file_path
              FROM tick_archive_unit AS unit
              JOIN tick_request AS request USING (request_id)
             WHERE request.schema = ANY(%s)
               AND unit.decoded_record_count IS NOT NULL
               AND unit.superseded_by_unit_id IS NULL
             ORDER BY unit.decoded_record_count DESC
             LIMIT %s
            """,
            (sorted(t.value for t in STORED_TIERS), LARGEST_UNITS),
        ).fetchall()
    reader = DbnFileReader()
    return [
        (int(u), int(n), reader.open_file(root / str(path)).record_size)
        for u, n, path in rows
    ]


def batch_count(records: int, record_size: int, budget: int) -> int:
    """What ``iter_batches`` yields: ``count = budget // record_size`` each."""
    return ceil(records / (budget // record_size))


def budget_memory(rise: dict[int, int], workers: int) -> tuple[float, float]:
    """``(per-worker bytes per budget byte, fixed bytes)``: a least-squares
    line through the runs' memory rises against their budgets. The intercept
    is what every run pays regardless of budget (plans, definitions,
    calendars, connections); the slope over the workers in flight is what a
    worker holds per byte of budget (found 2026-10-03: the raw rise was
    469 MiB even at 8 MiB, almost all of it fixed)."""
    xs, ys = list(rise), [float(rise[b]) for b in rise]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / var
    return slope / workers, mean_y - slope * mean_x


def verdict(
    seconds: dict[int, float], rise: dict[int, int], workers: int = TICK_INGEST_WORKERS
) -> str:
    current = TICK_DECODE_BATCH_BYTES
    per_worker, _ = budget_memory(rise, workers)
    if per_worker > RSS_BUDGET_MULTIPLE:
        return (
            f"change: a worker holds {per_worker:.1f}× its budget, over "
            f"{RSS_BUDGET_MULTIPLE}×"
        )
    for budget, took in seconds.items():
        moved = abs(took - seconds[current]) / seconds[current]
        if (
            budget != current
            and moved > TIME_CHANGE_FRACTION
            and took < seconds[current]
        ):
            return (
                f"change to {mib(budget)}: units {moved:.0%} faster than at "
                f"{mib(current)}"
            )
    return (
        f"keep {mib(current)}: a worker holds {per_worker:.1f}× its budget "
        f"(≤ {RSS_BUDGET_MULTIPLE}×) and no "
        f"budget faster by more than {TIME_CHANGE_FRACTION:.0%}"
    )


def run() -> Path:
    urls = load_proof_urls()
    require_free_space()
    units = largest_units(urls.db_url, archive_root())
    report = Report("batch", "Proof: decode batch budget 8/32/128 MiB (slice 226)")
    report.table(("unit", "records", "record bytes"), units)
    ids = tuple(u for u, _, _ in units)
    seconds: dict[int, float] = {}
    rise: dict[int, int] = {}
    rows = []
    for budget in BUDGETS:
        reset(urls)
        before = reset_own_peak_rss()
        with patched("TICK_DECODE_BATCH_BYTES", budget):
            done: IngestRun = ingest(urls, ids)
        rise[budget] = own_status_bytes("VmHWM") - before
        seconds[budget] = sum(u["duration_seconds"] for u in done.units)
        batches = sum(batch_count(n, size, budget) for _, n, size in units)
        rows.append(
            (
                mib(budget),
                f"{seconds[budget]:.1f}",
                mib(rise[budget]),
                batches,
                done.failed,
            )
        )
    report.table(("budget", "Σ unit s", "peak RSS rise", "batches", "failed"), rows)
    report.add(
        f"**Verdict:** {verdict(seconds, rise)}",
        "",
        f"Proof database reloaded: {reload_whole_set(urls):,} rows.",
    )
    return report.write()
