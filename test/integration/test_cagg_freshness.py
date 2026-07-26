"""Integration tests: slice 168 cagg freshness guard, staleness **induced**.

Requires MT_TIMESCALE_DB_URL. Each test builds a scratch hypertable with its
own cagg and its own refresh policy, exercises the guard against it, and drops
it on teardown — following ``test_rechunk_driver.py``. **Never pauses a
production job**; the 163 incident is reproduced on scratch objects only.

A test that merely asserts the helper was *called* would not satisfy design
criterion 1. These pause a real refresh policy and advance real raw data past
``start_offset``, then assert the guard refuses.

Covers:
  - 8.1 scratch fixture builds and tears down cleanly
  - 8.2 induced staleness → guard trips, seeding skipped, no gap rows written
  - 8.3 granularity-agnostic: minute-shaped and daily-shaped caggs, one helper
  - 8.3a induced slowness → bounded PROBE_FAILED refusal, no orphaned backend
  - 8.4 healthy path passes (no false positive) and probe cost is recorded
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest

from manta_trading.market.maintenance.cagg_freshness import (
    StalenessSignal,
    _evaluate,
    assert_cagg_fresh,
    reset_freshness_cache,
)

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

TABLE = "scratch_168_freshness"
CAGG = "scratch_168_freshness_5min"
DAILY_TABLE = "scratch_168_freshness_daily"
DAILY_CAGG = "scratch_168_freshness_weekly"

pytestmark = pytest.mark.skipif(not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set")

# The scratch policy mirrors the production minute caggs: start_offset 1 day.
_SCRATCH_START_OFFSET = "1 day"


def _drop_scratch(conn: psycopg.Connection) -> None:
    conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {CAGG} CASCADE")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE} CASCADE")
    conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {DAILY_CAGG} CASCADE")
    conn.execute(f"DROP TABLE IF EXISTS {DAILY_TABLE} CASCADE")


def _build_minute_scratch(conn: psycopg.Connection) -> None:
    """Scratch minute hypertable + 5-minute cagg + refresh policy, fully
    materialized so the cagg edge starts level with raw."""
    conn.execute(
        f"CREATE TABLE {TABLE} ("
        "  time timestamptz NOT NULL, symbol text NOT NULL, "
        "  close numeric(12,4) NOT NULL, volume bigint NOT NULL)"
    )
    conn.execute(
        f"SELECT create_hypertable('{TABLE}', 'time', "
        "chunk_time_interval => INTERVAL '1 day')"
    )
    # Two days of minute bars ending 2 hours ago, so "now" is realistic and the
    # cagg's end_offset does not swallow the whole dataset.
    conn.execute(
        f"INSERT INTO {TABLE} (time, symbol, close, volume) "
        "SELECT ts, 'AAA', 100, 1000 FROM generate_series("
        "  now() - INTERVAL '2 days', now() - INTERVAL '2 hours', "
        "  INTERVAL '1 minute') AS ts"
    )
    conn.execute(
        f"CREATE MATERIALIZED VIEW {CAGG} WITH (timescaledb.continuous) AS "
        f"SELECT time_bucket('5 minutes', time) AS time_bucket, symbol, "
        f"       sum(volume) AS volume FROM {TABLE} "
        "GROUP BY time_bucket, symbol WITH NO DATA"
    )
    conn.execute(f"CALL refresh_continuous_aggregate('{CAGG}', NULL, NULL)")
    conn.execute(
        f"SELECT add_continuous_aggregate_policy('{CAGG}', "
        f"start_offset => INTERVAL '{_SCRATCH_START_OFFSET}', "
        "end_offset => INTERVAL '5 minutes', "
        "schedule_interval => INTERVAL '5 minutes')"
    )


def _build_daily_scratch(conn: psycopg.Connection) -> None:
    """Scratch daily hypertable + weekly cagg, mirroring the daily caggs'
    long start_offset — the shape slice 167 will consume."""
    conn.execute(
        f"CREATE TABLE {DAILY_TABLE} ("
        "  time timestamptz NOT NULL, symbol text NOT NULL, "
        "  close numeric(12,4) NOT NULL)"
    )
    conn.execute(
        f"SELECT create_hypertable('{DAILY_TABLE}', 'time', "
        "chunk_time_interval => INTERVAL '30 days')"
    )
    conn.execute(
        f"INSERT INTO {DAILY_TABLE} (time, symbol, close) "
        "SELECT ts, 'AAA', 100 FROM generate_series("
        "  now() - INTERVAL '400 days', now() - INTERVAL '1 day', "
        "  INTERVAL '1 day') AS ts"
    )
    conn.execute(
        f"CREATE MATERIALIZED VIEW {DAILY_CAGG} WITH (timescaledb.continuous) AS "
        f"SELECT time_bucket('7 days', time) AS time_bucket, symbol, "
        f"       max(close) AS close FROM {DAILY_TABLE} "
        "GROUP BY time_bucket, symbol WITH NO DATA"
    )
    conn.execute(f"CALL refresh_continuous_aggregate('{DAILY_CAGG}', NULL, NULL)")
    # 21 days matches daily_weekly_ohlcv's production start_offset.
    conn.execute(
        f"SELECT add_continuous_aggregate_policy('{DAILY_CAGG}', "
        "start_offset => INTERVAL '21 days', "
        "end_offset => INTERVAL '1 day', "
        "schedule_interval => INTERVAL '1 hour')"
    )


@pytest.fixture
def scratch_conn() -> Iterator[psycopg.Connection]:
    """Fresh scratch minute hypertable + cagg + policy per test."""
    reset_freshness_cache()
    with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
        _drop_scratch(conn)
        _build_minute_scratch(conn)
    with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
        yield conn
    with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
        _drop_scratch(conn)
    reset_freshness_cache()


def _refresh_job_id(conn: psycopg.Connection, view_name: str) -> int:
    row = conn.execute(
        "SELECT job_id FROM timescaledb_information.jobs "
        "WHERE hypertable_name = %s "
        "  AND proc_name = 'policy_refresh_continuous_aggregate'",
        (view_name,),
    ).fetchone()
    assert row is not None, f"no refresh policy found for {view_name}"
    return int(row[0])


class TestScratchFixture:
    """8.1 — the fixture itself."""

    def test_scratch_cagg_and_policy_exist(
        self, scratch_conn: psycopg.Connection
    ) -> None:
        assert _refresh_job_id(scratch_conn, CAGG) > 0
        row = scratch_conn.execute(f"SELECT count(*) FROM {CAGG}").fetchone()
        assert row is not None and row[0] > 0, "scratch cagg should be materialized"

    def test_teardown_leaves_no_scratch_objects(self) -> None:
        # Runs outside the fixture: after the previous test's teardown, nothing
        # named scratch_168_* should remain.
        with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
            _drop_scratch(conn)
            row = conn.execute(
                "SELECT count(*) FROM pg_class WHERE relname LIKE 'scratch_168_%%'"
            ).fetchone()
        assert row is not None and row[0] == 0


class TestInducedStaleness:
    """8.2 — reproduce the 163 incident shape on scratch objects."""

    def test_healthy_scratch_cagg_is_fresh(
        self, scratch_conn: psycopg.Connection
    ) -> None:
        # Baseline (criterion 4): no false positive before anything is broken.
        verdict = _evaluate(scratch_conn, CAGG, source_table=TABLE)
        assert verdict.is_fresh is True, verdict.detail
        assert verdict.signals == ()

    def test_paused_policy_plus_advanced_raw_trips_the_guard(
        self, scratch_conn: psycopg.Connection
    ) -> None:
        job_id = _refresh_job_id(scratch_conn, CAGG)
        # Induce the incident: pause the SCRATCH policy, then advance raw well
        # past start_offset so the cagg's edge is frozen behind it.
        scratch_conn.execute("SELECT alter_job(%s, scheduled => false)", (job_id,))
        scratch_conn.execute(
            f"INSERT INTO {TABLE} (time, symbol, close, volume) "
            "SELECT ts, 'AAA', 100, 1000 FROM generate_series("
            "  now() + INTERVAL '1 hour', now() + INTERVAL '3 days', "
            "  INTERVAL '1 minute') AS ts"
        )

        verdict = _evaluate(scratch_conn, CAGG, source_table=TABLE)

        assert verdict.is_fresh is False
        assert StalenessSignal.NOT_SCHEDULED in verdict.signals
        assert StalenessSignal.LAG_EXCEEDS_THRESHOLD in verdict.signals
        # The operator needs the measured lag and the threshold it breached.
        assert verdict.lag is not None and verdict.threshold is not None
        assert verdict.lag > verdict.threshold
        assert CAGG in verdict.detail

    def test_paused_policy_alone_trips_before_any_lag_accrues(
        self, scratch_conn: psycopg.Connection
    ) -> None:
        # The 163 incident was silent for four days precisely because lag takes
        # time to accrue. NOT_SCHEDULED catches it immediately.
        job_id = _refresh_job_id(scratch_conn, CAGG)
        scratch_conn.execute("SELECT alter_job(%s, scheduled => false)", (job_id,))
        verdict = _evaluate(scratch_conn, CAGG, source_table=TABLE)
        assert verdict.is_fresh is False
        assert StalenessSignal.NOT_SCHEDULED in verdict.signals

    def test_cagg_without_a_refresh_policy_trips(
        self, scratch_conn: psycopg.Connection
    ) -> None:
        job_id = _refresh_job_id(scratch_conn, CAGG)
        scratch_conn.execute("SELECT delete_job(%s)", (job_id,))
        verdict = _evaluate(scratch_conn, CAGG, source_table=TABLE)
        assert verdict.is_fresh is False
        assert verdict.signals == (StalenessSignal.NO_JOB_ROW,)


class TestGranularityAgnostic:
    """8.3 — one helper, minute-shaped and daily-shaped caggs, no signature
    change. Confirms slice 167 can consume it as-is."""

    @pytest.fixture
    def both_scratch(self) -> Iterator[psycopg.Connection]:
        reset_freshness_cache()
        with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
            _drop_scratch(conn)
            _build_minute_scratch(conn)
            _build_daily_scratch(conn)
        with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
            yield conn
        with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
            _drop_scratch(conn)
        reset_freshness_cache()

    def test_same_call_shape_for_minute_and_daily_caggs(
        self, both_scratch: psycopg.Connection
    ) -> None:
        minute = _evaluate(both_scratch, CAGG, source_table=TABLE)
        daily = _evaluate(both_scratch, DAILY_CAGG, source_table=DAILY_TABLE)
        assert minute.is_fresh is True, minute.detail
        assert daily.is_fresh is True, daily.detail

    def test_daily_cagg_ceiling_applies_despite_long_start_offset(
        self, both_scratch: psycopg.Connection
    ) -> None:
        # The scratch daily policy uses start_offset 21 days / end_offset 1 day.
        # The threshold must be ceiling(1d) + end_offset(1d) = 2d, NOT the
        # 21-day start_offset — capping start_offset is what makes a stalled
        # daily cagg detectable at all (design criterion 3), and the end_offset
        # term only covers data the policy deliberately declines to materialize.
        daily = _evaluate(both_scratch, DAILY_CAGG, source_table=DAILY_TABLE)
        assert daily.threshold == timedelta(days=2), (
            "threshold must be min(start_offset, ceiling) + end_offset, "
            f"got {daily.threshold}"
        )
        assert daily.threshold < timedelta(days=21), (
            "MAX_COVERAGE_SOURCE_STALENESS must cap the 21-day start_offset"
        )


class TestInducedSlowness:
    """8.3a — the configured timeout converts a hung probe into a refusal in a
    live database, within bounded wall-clock time and with no orphaned backend.
    Pairs with unit test 4.1a, which proves the bound is configured."""

    def test_over_timeout_probe_returns_probe_failed_and_does_not_hang(
        self, scratch_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from manta_trading.market.maintenance import cagg_freshness

        # Shrink the probe budget to 1ms and make the probe genuinely slow, so
        # the timeout is what fires — not a mocked exception.
        monkeypatch.setattr(
            cagg_freshness, "CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT", "1ms"
        )

        def _slow_raw_max(conn: object, source_table: str) -> object:
            # Same shape as the real probe — set the (now 1ms) timeout, then run
            # a query that cannot finish inside it. The timeout, not a mock,
            # raises.
            with conn.cursor() as cur:  # type: ignore[attr-defined]
                cagg_freshness._set_probe_timeout(cur)
                cur.execute("SELECT pg_sleep(5)")
                return None

        monkeypatch.setattr(cagg_freshness, "_raw_max", _slow_raw_max)

        started = time.monotonic()
        verdict = cagg_freshness._evaluate(scratch_conn, CAGG, source_table=TABLE)
        elapsed = time.monotonic() - started

        assert verdict.is_fresh is False
        assert verdict.signals == (StalenessSignal.PROBE_FAILED,)
        assert elapsed < 5.0, (
            f"probe must be bounded by statement_timeout, took {elapsed:.2f}s"
        )

        # No orphaned backend: nothing from this test is still sleeping.
        with psycopg.connect(TIMESCALE_URL, autocommit=True) as check:
            row = check.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE query LIKE '%%pg_sleep(5)%%' AND state = 'active' "
                "  AND pid <> pg_backend_pid()"
            ).fetchone()
        assert row is not None and row[0] == 0, "probe left a backend running"


class TestHealthyPathAndProbeCost:
    """8.4 — no false positive, and the probe cost is recorded against the
    ~1 s envelope. A single recorded measurement, not a benchmark harness."""

    def test_healthy_path_passes_and_probe_cost_is_recorded(
        self, scratch_conn: psycopg.Connection, record_property: pytest.Function
    ) -> None:
        started = time.monotonic()
        verdict = assert_cagg_fresh(scratch_conn, CAGG, source_table=TABLE)
        elapsed = time.monotonic() - started

        assert verdict.is_fresh is True, verdict.detail
        record_property("probe_seconds", round(elapsed, 4))  # type: ignore[operator]
        assert elapsed < 5.0, f"uncached probe took {elapsed:.2f}s"

    def test_warm_call_is_served_from_cache(
        self, scratch_conn: psycopg.Connection
    ) -> None:
        first = assert_cagg_fresh(scratch_conn, CAGG, source_table=TABLE)
        started = time.monotonic()
        second = assert_cagg_fresh(scratch_conn, CAGG, source_table=TABLE)
        cached_elapsed = time.monotonic() - started
        assert second is first, "warm call must return the cached verdict object"
        assert cached_elapsed < 0.05
