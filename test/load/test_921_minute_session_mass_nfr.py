"""Load test for slice 921's minute-session-mass read bound.

The health check judges how much data the last completed trading session
actually holds. The slice states the read NFR: one session is on the order of
41,000 rows in the coarse minute cagg, and the read is expected to be
sub-second.

**Why this needs a load test rather than a unit test.** The read degrades to
``EXIT_UNAVAILABLE`` (exit 2) on a statement timeout, which an operator cannot
distinguish from the silence this slice exists to end — a check that times out
reports "could not run", exactly as uninformative as the age-based check that
missed the 2026-09-07 collapse. So the bound is a correctness property of the
check, not a nicety. Precedents: ``test_167_data_status_nfr.py`` and
``test_169_coverage_freshness_probe_nfr.py``, the latter asserting a margin
rather than merely "under the timeout".

**Scale honesty.** This runs against ``session_mass_db``, NOT
``prod_shaped_db``. The latter seeds one bar per symbol per 7-day bucket from
2010 and no ``trading_sessions`` rows at all, so the mass query's
``[session_open, session_close)`` window is empty against it: the test would
pass in milliseconds and the NFR would never be exercised. ``session_mass_db``
seeds one dense NYSE session — ~11,000 symbols across four 4-hour buckets,
~1.98M raw bars — which is the shape production measured on 2026-08-27.

**CI.** This repo's ``.github/workflows/ci.yml`` runs no test job at all
(a repo-wide gap tracked as slice 907), so the gate is a documented manual
run, recorded here and in runbook 100:

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_921_minute_session_mass_nfr.py

Requires ``MT_TIMESCALE_TEST_URL``. This tier never reads the production DB
URL; ``test_load_tier_never_references_prod_db_url`` in the 167 module
enforces that across every file in this directory, including this one.
"""

from __future__ import annotations

import os
import time as _time
from datetime import UTC, datetime

import psycopg
import pytest

from manta_trading.cli.commands.minute_session_mass import (
    fetch_candidate_sessions,
    fetch_session_mass,
    select_judged_session,
)
from manta_trading.constants import (
    HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE,
    HEALTH_MINUTE_SESSION_MIN_SYMBOLS,
    HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT,
)

#: The budget this test asserts, in seconds.
#:
#: HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT is 30 s and, critically, a timeout
#: makes the check report "could not run" rather than a verdict. A margin is
#: asserted rather than the raw ceiling so a regression is caught while it is
#: still a slowdown, not once it has become an outage — the same reasoning
#: test_169_coverage_freshness_probe_nfr.py applies to its sibling probe.
MASS_READ_BUDGET_SECONDS = 3.0

#: Sanity bound on the fixture itself: if the seeded session stops being dense,
#: a fast read proves nothing.
MIN_EXPECTED_BARS = 1_500_000

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    # Seeding ~1.98M bars plus one cagg refresh far exceeds the 30 s ini
    # default, as in the 167 and 169 load modules.
    pytest.mark.timeout(600),
]


#: The instant these tests judge from — the day after the fixture's dense
#: session, past its 04:05 UTC judging boundary.
JUDGED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _judged_session(conn):
    """The judged session, as the health check itself resolves it."""
    return select_judged_session(
        fetch_candidate_sessions(conn, now=JUDGED_AT), now=JUDGED_AT
    )


def test_the_fixture_actually_loads_the_query(session_mass_db: str) -> None:
    """Guard the guard: assert the seeded session is dense before timing it."""
    with psycopg.connect(session_mass_db) as conn:
        session = _judged_session(conn)
        assert session is not None, "the fixture must produce a judged session"
        mass = fetch_session_mass(conn, session)

    assert mass.total_bars >= MIN_EXPECTED_BARS, (
        f"the fixture seeded only {mass.total_bars:,} bars — a read over a "
        "sparse session does not exercise the bound"
    )
    assert mass.symbols_meeting_min_bars >= HEALTH_MINUTE_SESSION_MIN_SYMBOLS


def test_the_mass_read_stays_well_inside_its_budget(session_mass_db: str) -> None:
    """Criterion: the session-mass read completes well inside its timeout."""
    with psycopg.connect(session_mass_db) as conn:
        session = _judged_session(conn)
        assert session is not None

        started = _time.perf_counter()
        mass = fetch_session_mass(conn, session)
        elapsed = _time.perf_counter() - started

    assert mass.total_bars > 0
    assert elapsed < MASS_READ_BUDGET_SECONDS, (
        f"the minute-session-mass read took {elapsed:.2f} s against a "
        f"{MASS_READ_BUDGET_SECONDS:.1f} s budget "
        f"(statement timeout {HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT}). "
        "On timeout the health check reports 'could not run', which an "
        "operator cannot distinguish from the silence slice 921 removed."
    )


def test_the_candidate_fetch_is_bounded(session_mass_db: str) -> None:
    """The candidate read is a small LIMITed scan, not a calendar walk."""
    with psycopg.connect(session_mass_db) as conn:
        started = _time.perf_counter()
        candidates = fetch_candidate_sessions(conn, now=JUDGED_AT)
        elapsed = _time.perf_counter() - started

    assert candidates, "the fixture seeds sessions, so candidates must be found"
    assert elapsed < MASS_READ_BUDGET_SECONDS
    # Regression: trading_sessions is populated ~2 years ahead
    # (TRADING_SESSIONS_EXTENSION_YEARS), so an unbounded "newest first" read
    # returns only future rows and nothing is ever judgeable. The fixture's
    # calendar runs to 2028-12-29, which is what caught this.
    assert all(c.session_close_utc <= JUDGED_AT for c in candidates), (
        "candidates must be sessions that have already closed"
    )


def test_the_dense_session_is_judged_healthy(session_mass_db: str) -> None:
    """End to end: a production-shaped session passes both mass floors, so the
    thresholds are not merely small enough to pass on an empty fixture."""
    from manta_trading.cli.commands.minute_session_mass import (
        check_minute_session_mass,
    )

    with psycopg.connect(session_mass_db) as conn:
        session = _judged_session(conn)
        assert session is not None
        mass = fetch_session_mass(conn, session)

    ok, detail = check_minute_session_mass(session, mass)
    assert ok is True, detail
    assert f"floor {HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE:,}" in detail
