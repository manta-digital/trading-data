"""Load test for slice 167's sub-second NFR (D5, success criterion 6).

A full-universe ``data_status`` read — through the guarded accessor, with the
coverage-freshness probe inside the measured path — must complete in under
``_NFR_SECONDS`` at production shape.

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

_NFR_SECONDS = 1.0
"""D5 / success criterion 6: full-universe data_status read under one second."""

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
        f"(NFR < {_NFR_SECONDS:.1f}s)"
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
