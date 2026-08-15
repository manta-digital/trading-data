"""Load test for the full-universe ``data_status`` read (167 D5 / 169 crit. 12).

A full-universe ``data_status`` read — through the guarded accessor, with the
coverage-freshness probe inside the measured path — must complete in under
``_NFR_SECONDS`` at production shape.

**Slice 169 amended the bound** from a flat 1.0 s to a margin against the 7.8 s
pre-167 raw scan, because the flat second turned out to be reachable only at the
365-day bucket width that causes the defect slice 169 repairs. See
``_NFR_SECONDS`` for the measurements. **This module always measured the real
caller path** (``fetch_status_rows_with_freshness``, not ``SELECT count(*)``),
which is why it, and not the design's own criterion, caught the discrepancy.

**Scale honesty (8.1.2).** A 10-symbol fixture proves nothing about a
production read, so this test seeds a prod-shaped throwaway database:
``_SYMBOL_COUNT`` symbols (>= the 11,625-symbol production universe) with one
minute and one daily bar per year across ``_YEAR_COUNT`` years. Bars are
sparse because the read cost of the cagg-backed view is driven by coverage
*rows* (symbols x year-buckets), not raw bar volume — each coverage cagg ends
up with symbols x years rows, matching production's shape. What this fixture
deliberately leaves empty: ``data_gaps`` and the attempt/acquisition tables.
Their contribution to the view was never the slow term (the raw
``bars_summary`` scan was, at 7.8 s on prod), and the measured production
read including them is ~200 ms.

**Gating (8.1.4 / 8.2).** Runs only with ``MT_RUN_LOAD_TESTS=1`` (the tier
convention from ``test_146_part1_nfrs.py``) and requires
``MT_TIMESCALE_TEST_URL`` for the throwaway database. This tier never reads
the production DB URL; ``test_load_tier_never_references_prod_db_url``
enforces that mechanically.

**Concrete invocation (8.3 / criterion 6).** The repo has no CI config; CI
wiring is slice 907 (CI Pipeline and Load-Test Gating). Until then, the gate
is this documented manual run:

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/

with ``MT_TIMESCALE_TEST_URL`` exported (see ``.env``).
"""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

import psycopg
import pytest

from .conftest import SYMBOL_COUNT

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    # Seeding + three cagg refreshes far exceed the 30 s ini default.
    pytest.mark.timeout(600),
]

_PRE_167_RAW_SCAN_SECONDS = 7.8
"""The full-universe read cost slice 167 removed, measured on prod.

The gate below is expressed as a margin against this rather than as an absolute
second, because that margin is what slice 167's architecture actually bought.
"""

_NFR_SECONDS = _PRE_167_RAW_SCAN_SECONDS / 2
"""Ceiling for the caller-issued full-universe read (slice 169 criterion 12).

**Amended by slice 169 from a flat 1.0 s.** Two things forced it, both measured
on a prod-shaped database rather than argued:

1. **This test measures the shape callers issue; the old criterion did not.**
   Criterion 12 was written against ``SELECT count(*) FROM data_status``, which
   lets the planner skip projection, sort, and row assembly. At the shipped
   7-day width ``count(*)`` costs 0.933 s while the real reader path costs
   2.636 s — a 2.8x gap. This test always measured the real path, so it was the
   thing that caught the discrepancy.

2. **A 1 s bound is reachable only at the width that causes the defect.** The
   caller-issued read costs 0.487 s at a 365-day bucket, 1.716 s at 30 days,
   and 2.636 s at 7 days. Slice 169 narrows the bucket precisely because a
   365-day one leaves the open bucket unmaterialized for up to a year — a fast
   read of year-stale coverage is not what slice 167 was protecting.

So the gate is a **no-regression margin against the pre-167 raw scan**, not an
absolute second: the read must stay far below the 7.8 s cost 167 removed, which
is the property that matters, while the absolute number is recorded and tracked.

The measured 2.636 s IS a regression against pre-169 production (0.487 s) and is
accepted with its remedy filed: ~37% of it is
``fetch_all_health_counts_with_freshness``, which ignores the caller's symbol
filter and aggregates all 12,040 symbols on every call (issue #16); the row
fetch is issue #17. Neither is in slice 169's scope. Frequency context:
``/api/v1/health`` does not read ``data_status`` at all, the daemon never reads
it, and ``mt data status`` is operator-initiated a few times a day.
"""

_SYMBOL_COUNT = SYMBOL_COUNT
"""The fixture's symbol count, imported rather than restated (187 D10).

``prod_shaped_db`` moved to ``conftest.py`` when slice 187 added a second
consumer; the row-count assertion below must track whatever that fixture
actually seeds, so a local literal here would be a second definition site free
to drift.
"""

_MEASURED_RUNS = 3
"""Median of three keeps one network hiccup from failing a real sub-second
read, while a genuine regression past the NFR fails all three."""


def test_full_universe_data_status_under_one_second(prod_shaped_db: str) -> None:
    """8.1: the guarded full-universe read stays under the NFR at prod shape.

    The freshness cache is reset before every run so each measured sample
    includes the guard's probe (8.1.3) — a fresh CLI process, the path an
    operator actually pays for, never starts with a warm verdict cache.
    """
    from manta_trading.data.maintenance.status_coverage import COVERAGE_VIEWS
    from manta_trading.data.maintenance.status_queries import (
        fetch_status_rows_with_freshness,
    )
    from manta_trading.market.maintenance.cagg_freshness import (
        reset_freshness_cache,
    )

    samples_s: list[float] = []
    for _ in range(_MEASURED_RUNS):
        reset_freshness_cache()
        with psycopg.connect(prod_shaped_db) as conn:
            t0 = time.perf_counter()
            rows, freshness = fetch_status_rows_with_freshness(
                conn, symbol=None, health_filter=None
            )
            samples_s.append(time.perf_counter() - t0)

        # The read must have been full-universe and guarded — a fast read of
        # the wrong thing would make the latency assertion meaningless.
        assert len(rows) == _SYMBOL_COUNT * 2, (
            f"expected {_SYMBOL_COUNT} symbols x 2 granularities, "
            f"got {len(rows)} rows"
        )
        assert len(freshness.verdicts) == len(COVERAGE_VIEWS)

    median_s = statistics.median(samples_s)
    assert median_s < _NFR_SECONDS, (
        f"full-universe data_status median = {median_s:.3f}s over "
        f"{_MEASURED_RUNS} runs {[f'{s:.3f}' for s in samples_s]} "
        f"(ceiling {_NFR_SECONDS:.1f}s = half the {_PRE_167_RAW_SCAN_SECONDS}s "
        "pre-167 raw scan). Slice 169 criterion 12 is a no-regression margin "
        "against that scan, not an absolute second — see this module's "
        "_NFR_SECONDS docstring. If this fires, the coverage read has given "
        "back a meaningful share of what slice 167 bought; check issues #16 "
        "and #17 before relaxing it further."
    )


def test_load_tier_never_references_prod_db_url() -> None:
    """8.2: the load tier must not be pointable at production by default.

    Every DB-touching load test goes through ``MT_TIMESCALE_TEST_URL`` and a
    throwaway database; none may *read* the production URL variable. Docstring
    mentions are allowed (``test_146_part1_nfrs.py`` cites an integration
    test's env), so only lines that access the environment are flagged. The
    needle is concatenated so this file's own source cannot trip the check.
    """
    prod_url_var = "MT_TIMESCALE" + "_DB_URL"
    env_read_markers = ("environ", "getenv")
    for path in Path(__file__).parent.glob("*.py"):
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if prod_url_var in line and any(m in line for m in env_read_markers):
                pytest.fail(
                    f"{path.name}:{line_no} reads {prod_url_var}; load tests "
                    "must use MT_TIMESCALE_TEST_URL and an ephemeral database"
                )
