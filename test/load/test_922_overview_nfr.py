"""Load test for ``mt data overview`` at production shape (slice 922).

**The ten-second budget, split.** Design 922 Decision 12 gives the whole
command ten seconds. That splits into two independent halves:

- **five seconds of database work**, which is what this test measures: the
  pass_runs reads for all five kinds plus the four newest-row probes;
- **five seconds for the one HTTPS call**, which is not measured here but
  *bounded by construction* — ``fetch_credit_usage`` passes
  ``EODHD_ACCOUNT_TIMEOUT_SECONDS`` to a single GET with no retry, so it
  cannot exceed its half however slow the provider is.

Measuring them separately is what makes the bound meaningful. A test that
timed the whole command would be measuring the network, and would either be
flaky or would have to stub the call anyway. This test stubs it — load tests
do not reach the network — and asserts the half the code is responsible for.

**Scale honesty.** ``prod_shaped_db`` seeds the production-shaped universe
(see ``conftest.py``); this module additionally seeds ``pass_runs`` with more
rows than production will hold in a year, so the newest-first and
latest-ended queries are measured against an index that has to work rather
than a table the planner can scan for free.

**Gating.** Runs only with ``MT_RUN_LOAD_TESTS=1`` and requires
``MT_TIMESCALE_TEST_URL``. This repository has no CI test job
(``.github/workflows/ci.yml`` publishes on tags only), so every tier is run
locally, the same way the 167 case is:

    MT_RUN_LOAD_TESTS=1 python scripts/run_tests.py load

with ``MT_TIMESCALE_TEST_URL`` exported (see ``.env``).
"""

from __future__ import annotations

import os
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import psycopg
import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(600),
]

_TOTAL_BUDGET_SECONDS = 10.0
"""Design 922 Decision 12: the whole command, end to end."""

_CREDIT_CALL_BUDGET_SECONDS = 5.0
"""The HTTPS half, bounded by ``EODHD_ACCOUNT_TIMEOUT_SECONDS`` rather than
measured: one GET, no retry, so it cannot exceed this."""

_DB_BUDGET_SECONDS = _TOTAL_BUDGET_SECONDS - _CREDIT_CALL_BUDGET_SECONDS
"""What this test measures: everything ``gather`` reads from the database."""

_SEEDED_RUNS = 5_000
"""More pass_runs rows than a year of production writes.

Five kinds firing at their real cadences — minute daily, daily twice daily,
kalshi and health hourly, accounting daily — produce roughly 19,000 rows a
year, but the queries here are ``ORDER BY started_at DESC LIMIT 1`` and a
filtered open-row scan, both index-served. Five thousand rows is enough for a
missing index to show up as a sequential scan; the point is to measure an
index that works, not to reproduce a year.
"""

_MEASURED_RUNS = 3
"""Median of three: one network or autovacuum hiccup must not fail a real
sub-second read, while a genuine regression fails all three."""


def _seed_pass_runs(url: str, count: int) -> None:
    """Write ``count`` ended rows spread across every kind."""
    from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome

    kinds = list(PassKind)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(count):
        started = start + timedelta(minutes=index)
        rows.append(
            (
                uuid.uuid4(),
                str(kinds[index % len(kinds)]),
                "manta9000",
                1000 + index,
                started,
                started + timedelta(minutes=30),
                str(PassRunOutcome.COMPLETE),
                0,
                f"seeded row {index}",
            )
        )
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO pass_runs (run_id, pass, hostname, pid, started_at, "
                "ended_at, outcome, exit_code, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("ANALYZE pass_runs")


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.eodhd_api_key = "load-test-key"
    settings.minute_firing_days = (5,)
    return settings


def test_the_overviews_database_half_stays_within_budget(
    prod_shaped_db: str,
) -> None:
    """``gather``'s database work must fit its five-second half of the budget.

    The credit call is stubbed: load tests do not reach the network, and its
    half of the budget is enforced by the request timeout rather than here.
    """
    from manta_trading.api.eodhd_account import CreditUsage
    from manta_trading.cli.commands.overview import build_overview, gather

    _seed_pass_runs(prod_shaped_db, _SEEDED_RUNS)
    stub_credits = lambda _key: CreditUsage(65_210, 100_000, 0)  # noqa: E731

    samples_s: list[float] = []
    for _ in range(_MEASURED_RUNS):
        # A fresh connection every run: an operator pays the connect cost on
        # every invocation, and a pooled measurement would understate it.
        with psycopg.connect(prod_shaped_db) as conn:
            t0 = time.perf_counter()
            facts = gather(conn, _settings(), fetch_credits=stub_credits)
            samples_s.append(time.perf_counter() - t0)

    median_s = statistics.median(samples_s)
    assert median_s < _DB_BUDGET_SECONDS, (
        f"gather's database half took {median_s:.3f} s at production shape, "
        f"over its {_DB_BUDGET_SECONDS} s budget (samples: "
        f"{[f'{s:.3f}' for s in samples_s]})"
    )

    # The read must have been the real one: every kind answered, and the
    # seeded rows found. A fast read of nothing proves nothing.
    from manta_trading.data.acquisition.pass_runs import PassKind

    assert all(facts.latest_ended[kind] is not None for kind in PassKind)
    assert len(facts.sources) == 4

    # And the build stays negligible — it is pure, but it walks every row.
    t0 = time.perf_counter()
    build_overview(facts)
    build_s = time.perf_counter() - t0
    assert build_s < 0.1, f"build_overview took {build_s:.3f} s"

    print(
        f"\noverview gather (database half): median {median_s:.3f} s "
        f"of {_DB_BUDGET_SECONDS} s budget; samples "
        f"{[f'{s:.3f}' for s in samples_s]}; build {build_s:.4f} s"
    )


def test_the_credit_call_is_bounded_by_construction() -> None:
    """The other half of the budget, enforced by the timeout rather than timed.

    ``fetch_credit_usage`` makes one GET with no retry, so the worst case is
    the timeout it passes. If that constant ever grew past its half, the
    ten-second end-to-end bound would silently become unreachable.
    """
    from manta_trading.constants import EODHD_ACCOUNT_TIMEOUT_SECONDS

    assert EODHD_ACCOUNT_TIMEOUT_SECONDS <= _CREDIT_CALL_BUDGET_SECONDS
    assert _DB_BUDGET_SECONDS + EODHD_ACCOUNT_TIMEOUT_SECONDS <= _TOTAL_BUDGET_SECONDS
