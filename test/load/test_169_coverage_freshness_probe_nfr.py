"""Load tests for slice 169's restated NFRs (criteria 17 and 19).

Slice 169 Task B.8. Task B measured these once, by hand, on a prod-shaped
database. That is enough to *select* a bucket width but not enough to keep the
choice honest: a later change to the width, the offsets, or the coverage view
shape could regress either bound with nothing to catch it. These make both
re-checkable.

**Criterion 17 — the content-edge probe stays well inside its budget.**
``CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT`` is 10 s and, critically, **on timeout
the freshness check degrades to a refusal (PROBE_FAILED), not to a pass**. So a
probe that outgrows its budget makes ``mt data status``, ``/api/v1/status`` and
``/api/v1/health`` report unusable coverage — a failure indistinguishable to an
operator from the staleness slice 169 set out to clear. Design D3b is explicit
that a width pushing this near the budget is *rejected on NFR grounds* rather
than accommodated by raising the timeout, so this test asserts a margin, not
merely "under 10 s".

**Criterion 19 — a policy run fits its schedule interval.**
``refresh_continuous_aggregate`` over the configured ``start_offset`` must
complete comfortably inside the 1-hour ``schedule_interval``; a policy that
overruns re-creates the perpetually-behind head the slice exists to fix.

**Scale honesty.** Both run against ``prod_shaped_db``, which since slice 169
seeds ``BARS_PER_YEAR`` bars per symbol-year derived from
``COVERAGE_BUCKET_INTERVAL`` — so the coverage caggs materialize a row count
that actually scales with the width. Before that change the fixture seeded one
bar per symbol-year and was width-blind: 120,000 coverage rows at a 365-day
bucket and at a 7-day bucket alike, which would have let a width regression
pass unnoticed.

**Gating.** ``MT_RUN_LOAD_TESTS=1`` plus ``MT_TIMESCALE_TEST_URL`` (the tier
convention). This tier never reads the production DB URL;
``test_load_tier_never_references_prod_db_url`` in the 167 module enforces that
mechanically across every file in this directory, including this one.

**CI.** This repo's ``.github/workflows/ci.yml`` runs no test job at all, so
the gate is the documented manual run — the same gap slice 167's load test
already records, tracked as slice 907, not one this slice introduces:

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/
"""

from __future__ import annotations

import os
import statistics
import time

import psycopg
import pytest

from manta_trading.constants import (
    CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT,
    COVERAGE_SOURCE_TABLE,
    DAILY_COVERAGE_REFRESH_END_OFFSET,
    DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    DAILY_COVERAGE_REFRESH_START_OFFSET,
    DAILY_COVERAGE_VIEW,
    MINUTE_COVERAGE_REFRESH_END_OFFSET,
    MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_VIEW,
)

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    # Seeding a width-scaled prod-shaped database plus three cagg refreshes far
    # exceeds the 30 s ini default.
    pytest.mark.timeout(1800),
]

_PROBE_BUDGET_S = float(CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT.rstrip("s"))
"""Parsed from the constant so the budget cannot drift from what the reader
path actually enforces."""

_PROBE_MARGIN = 0.25
"""Criterion 17 requires the probe be "well inside" its budget, not merely
under it. A width whose probe lands at 9 s would technically pass a bare
`< 10 s` check while being one production growth spurt from turning every
status read into a PROBE_FAILED refusal. Measured on a prod-shaped database in
Task B the worst case was 0.220 s — 2% of budget — so a 25% ceiling leaves an
order of magnitude of headroom while still failing a genuine regression."""

_MEASURED_RUNS = 3
"""Median of three keeps one network hiccup from failing a healthy probe, while
a real regression fails all three."""

_COVERAGE_VIEWS = (MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW)


def _median_seconds(url: str, sql: str, runs: int = _MEASURED_RUNS) -> float:
    samples: list[float] = []
    for _ in range(runs):
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            start = time.perf_counter()
            cur.execute(sql)
            cur.fetchall()
            samples.append(time.perf_counter() - start)
    return statistics.median(samples)


@pytest.mark.parametrize("view_name", _COVERAGE_VIEWS)
def test_content_edge_probe_stays_well_inside_its_budget(
    prod_shaped_db: str, view_name: str
) -> None:
    """Criterion 17 / D3b.

    This is the exact query ``status_coverage._content_edge_lag`` issues, and
    unlike the generic bucket-lag probe it reads a plain aggregate column rather
    than ``time_bucket``, so it is **not** chunk-excludable — which is precisely
    why the design flagged it as the NFR this slice perturbs.
    """
    median_s = _median_seconds(
        prod_shaped_db, f"SELECT max(last_bucket) FROM {view_name}"
    )
    ceiling_s = _PROBE_BUDGET_S * _PROBE_MARGIN

    assert median_s < ceiling_s, (
        f"{view_name} content-edge probe median = {median_s:.3f}s, which is "
        f"{median_s / _PROBE_BUDGET_S:.0%} of the "
        f"{_PROBE_BUDGET_S:.0f}s CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT "
        f"(ceiling {ceiling_s:.1f}s). On timeout the freshness check refuses "
        "rather than passing, so this bound protects every data_status reader. "
        "Per D3b, raising the timeout is not the fix — it is a bound on reader "
        "latency, and widening it trades a refusal for a stall."
    )


@pytest.mark.parametrize("view_name", _COVERAGE_VIEWS)
def test_raw_source_probe_stays_well_inside_its_budget(
    prod_shaped_db: str, view_name: str
) -> None:
    """The other half of the freshness probe pair.

    ``assert_cagg_fresh`` reads both edges under the same statement timeout, so
    a slow *raw* probe produces the same PROBE_FAILED refusal as a slow cagg
    probe. Asserted separately because the two scale with different things —
    the cagg side with coverage rows, the raw side with the source hypertable's
    chunk count.
    """
    source = COVERAGE_SOURCE_TABLE[view_name]
    median_s = _median_seconds(prod_shaped_db, f"SELECT max(time) FROM {source}")
    ceiling_s = _PROBE_BUDGET_S * _PROBE_MARGIN

    assert median_s < ceiling_s, (
        f"raw edge probe on {source} median = {median_s:.3f}s "
        f"(ceiling {ceiling_s:.1f}s of a {_PROBE_BUDGET_S:.0f}s budget)"
    )


@pytest.mark.parametrize(
    ("view_name", "start_offset", "end_offset", "schedule_interval"),
    [
        (
            MINUTE_COVERAGE_VIEW,
            MINUTE_COVERAGE_REFRESH_START_OFFSET,
            MINUTE_COVERAGE_REFRESH_END_OFFSET,
            MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
        ),
        (
            DAILY_COVERAGE_VIEW,
            DAILY_COVERAGE_REFRESH_START_OFFSET,
            DAILY_COVERAGE_REFRESH_END_OFFSET,
            DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
        ),
    ],
)
def test_policy_run_fits_its_schedule_interval(
    prod_shaped_db: str,
    view_name: str,
    start_offset: object,
    end_offset: object,
    schedule_interval: object,
) -> None:
    """Criterion 19 / D4a.

    Issues the refresh the scheduled policy issues — same view, same offsets —
    and asserts it fits the interval with margin. A policy that overruns its
    schedule contends with the next run and with the daemon, re-creating the
    perpetually-behind head this slice repairs.

    **What this can and cannot prove.** The fixture is quiescent, so the
    refresh processes only the invalidations left by seeding. Task B measured
    the same shape with deliberate invalidations and found steady-state cost
    flat across a 47x range of ``start_offset`` (0.058 s at 16 days vs 0.072 s
    at 750), because invalidation tracking bounds the work to buckets that
    actually changed. So this guards against a *structural* regression — a
    window or width that makes even a near-empty refresh expensive — not
    against live-ingest contention, which only production can measure.
    """
    from datetime import timedelta

    assert isinstance(start_offset, timedelta)
    assert isinstance(end_offset, timedelta)
    assert isinstance(schedule_interval, timedelta)

    with psycopg.connect(prod_shaped_db, autocommit=True) as conn:
        start = time.perf_counter()
        conn.execute(
            f"CALL refresh_continuous_aggregate('{view_name}', "
            f"now() - INTERVAL '{int(start_offset.total_seconds())} seconds', "
            f"now() - INTERVAL '{int(end_offset.total_seconds())} seconds')"
        )
        elapsed_s = time.perf_counter() - start

    budget_s = schedule_interval.total_seconds() * 0.5
    assert elapsed_s < budget_s, (
        f"{view_name} policy run took {elapsed_s:.1f}s against a "
        f"{schedule_interval} schedule interval (ceiling {budget_s:.0f}s, half "
        "the interval so consecutive runs cannot overlap). A policy that "
        "overruns its schedule re-creates the perpetually-behind head slice "
        "169 repairs."
    )
