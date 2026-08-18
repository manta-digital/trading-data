"""Integration tests: does a refresh policy advance the cagg head *unaided*?

Slice 169 Task C.6a. Builds and proves the mechanism that part 2's Task G runs
against production for **criterion 18** — "the refresh policy advances the head
on its own" — the only success criterion the original defect could not satisfy.

**Why this needs its own mechanism.** Every other check in the slice's
walkthrough is satisfiable *by the bug*. Steps 5-7 run immediately after a
manual full-span refresh, and step 8 reads ``scheduled = true`` from the job
catalog — a manual write plus a green catalog row are precisely the two signals
that stayed green through all 205 successful no-op runs on production. Nothing
else distinguishes a working policy from the defect.

So ``_head_advanced`` requires **both** halves: the job ran (its
``last_successful_finish`` moved) **and** the cagg's head moved with it. A job
that ran while ``MAX(last_bucket)`` stood still is the original defect's exact
signature and must return ``False``.

**The scheduler is never manually triggered here.** ``CALL run_job()`` would be
faster and deterministic, but a manually-invoked policy is exactly what
criterion 18 does *not* assert — the defect would pass such a test. These tests
wait for the background scheduler to fire on its own, which a short
``schedule_interval`` makes practical (measured: a 15-second interval is
accepted and fires reliably).

**What the open-bucket test documents.** Task C.6a's brief suggested asserting
that rows written into a still-open bucket cause the head to advance. Measured
on TimescaleDB 2.29.1, that is false: 200 rows in the open bucket saw 13
consecutive successful policy runs materialize *nothing*. The open bucket is
never refreshed while open — at a 7-day width exactly as at 365 (slice design
D1: "It does not make the engine refresh an open bucket — nothing does").
Narrowing bounds how much that limitation can hide; it does not remove it. The
test below asserts the true behaviour, so the accepted residual is documented
rather than mistaken for a fix.

Runs against a throwaway database (``ephemeral_db``) on a scratch hypertable,
following ``test_rechunk_driver.py``'s precedent. Never touches a production
job or either real coverage cagg.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
import pytest

from manta_trading.constants import COVERAGE_BUCKET_INTERVAL

# These tests wait on the background scheduler, which the 30 s ini default
# cannot accommodate: proving the open-bucket NON-event requires burning a full
# tick window, and the scheduler is shared with every other job on the cluster.
pytestmark = pytest.mark.timeout(600)

_TABLE = "scratch_169_head"
_CAGG = "scratch_169_head_cov"

_SCHEDULE_INTERVAL = timedelta(seconds=15)
"""Short enough for a test to wait out, long enough that the scheduler is
reliably the thing firing it rather than a race with setup."""

_TICK_TIMEOUT = timedelta(seconds=180)
"""Generous ceiling: the scheduler is shared with every other job on the
cluster, so a tick can be delayed well past its nominal interval."""

_POLL = 3.0


@dataclass(frozen=True)
class HeadSample:
    """A point-in-time reading of the two signals criterion 18 compares."""

    cagg_head: datetime | None
    last_successful_finish: datetime | None


def _sample(conn: psycopg.Connection, job_id: int) -> HeadSample:
    with conn.cursor() as cur:
        cur.execute(f"SELECT max(last_bucket) FROM {_CAGG}")
        row = cur.fetchone()
        head = row[0] if row else None

        # A job that has never run reports last_successful_finish =
        # '-infinity', which psycopg refuses to convert ("timestamp too small
        # (before year 1)"). Normalise it to NULL server-side rather than
        # letting the driver raise — the two mean the same thing here, and
        # _head_advanced already treats None as "never ran".
        cur.execute(
            "SELECT nullif(last_successful_finish, '-infinity') "
            "FROM timescaledb_information.job_stats WHERE job_id = %s",
            (job_id,),
        )
        row = cur.fetchone()
        finish = row[0] if row else None

    return HeadSample(cagg_head=head, last_successful_finish=finish)


def _head_advanced(before: HeadSample, after: HeadSample) -> bool:
    """True only when the job ran **and** the cagg head moved with it.

    This is the check part 2's Task G runs against production. Both halves are
    required, and that is the whole point:

    - job ran but head stood still  -> the original defect. False.
    - head moved but job never ran  -> someone refreshed manually. False.
    - neither                       -> nothing happened. False.

    A ``None`` head means the cagg has never materialized anything, which is
    maximal staleness rather than an absence of lag, so it can only be the
    *before* side of an advance.
    """
    job_ran = after.last_successful_finish is not None and (
        before.last_successful_finish is None
        or after.last_successful_finish > before.last_successful_finish
    )
    if not job_ran:
        return False

    if after.cagg_head is None:
        return False
    if before.cagg_head is None:
        return True
    return after.cagg_head > before.cagg_head


def _wait_for_tick(
    url: str, job_id: int, before: HeadSample, *, expect_advance: bool
) -> HeadSample:
    """Poll until the head advances, or until the timeout expires.

    When ``expect_advance`` is False this always burns the full timeout — that
    is deliberate. Proving a *non*-event needs the scheduler to have had ample
    opportunity to fire, and the job-run assertion in the caller confirms it
    did fire rather than the test merely having outrun it.
    """
    deadline = time.monotonic() + _TICK_TIMEOUT.total_seconds()
    latest = before
    while time.monotonic() < deadline:
        time.sleep(_POLL)
        with psycopg.connect(url) as conn:
            latest = _sample(conn, job_id)
        if expect_advance and _head_advanced(before, latest):
            return latest
    return latest


def _create_scratch(conn: psycopg.Connection) -> int:
    """Scratch hypertable + cagg at the real width + a fast-ticking policy."""
    width_s = int(COVERAGE_BUCKET_INTERVAL.total_seconds())

    conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_CAGG} CASCADE")
    conn.execute(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")
    conn.execute(
        f"CREATE TABLE {_TABLE} "
        "(time TIMESTAMPTZ NOT NULL, symbol TEXT NOT NULL, v INT)"
    )
    conn.execute(
        f"SELECT create_hypertable('{_TABLE}', 'time', "
        "chunk_time_interval => INTERVAL '1 day')"
    )
    # Mirrors the real coverage caggs' shape, including the last_bucket content
    # column the head check reads.
    conn.execute(
        f"CREATE MATERIALIZED VIEW {_CAGG} WITH (timescaledb.continuous) AS "
        f"SELECT time_bucket(INTERVAL '{width_s} seconds', time) AS time_bucket, "
        "       symbol, count(*) AS bars, "
        "       min(time) AS first_bucket, max(time) AS last_bucket "
        f"FROM {_TABLE} GROUP BY 1, 2 WITH NO DATA"
    )
    row = conn.execute(
        f"SELECT add_continuous_aggregate_policy('{_CAGG}', "
        f"start_offset => INTERVAL '{365 * 86400} seconds', "
        "end_offset => INTERVAL '4 hours', "
        f"schedule_interval => INTERVAL "
        f"'{int(_SCHEDULE_INTERVAL.total_seconds())} seconds')"
    ).fetchone()
    assert row is not None
    return int(row[0])


def _seed(conn: psycopg.Connection, *, ago: timedelta, symbol: str, n: int) -> None:
    conn.execute(
        f"INSERT INTO {_TABLE} "
        "SELECT now() - %s::interval + (g || ' minutes')::interval, %s, 1 "
        "FROM generate_series(1, %s) g",
        (ago, symbol, n),
    )


@pytest.fixture
def scratch(ephemeral_db: str):
    """Yields ``(url, job_id)``; drops the scratch objects on teardown."""
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)

    with psycopg.connect(ephemeral_db, autocommit=True) as conn:
        job_id = _create_scratch(conn)

    yield ephemeral_db, job_id

    with psycopg.connect(ephemeral_db, autocommit=True) as conn:
        conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_CAGG} CASCADE")
        conn.execute(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")


class TestHeadAdvancedHelper:
    """The helper's own contract, exercised without a database.

    Cheap to run and it pins the part that must not quietly weaken: a job that
    ran while the head stood still is the defect, and must be False.
    """

    _T0 = datetime(2026, 8, 1, 12, 0)
    _T1 = datetime(2026, 8, 1, 13, 0)

    def test_job_ran_and_head_moved_is_true(self) -> None:
        before = HeadSample(cagg_head=self._T0, last_successful_finish=self._T0)
        after = HeadSample(cagg_head=self._T1, last_successful_finish=self._T1)
        assert _head_advanced(before, after) is True

    def test_job_ran_but_head_stood_still_is_false(self) -> None:
        """THE ORIGINAL DEFECT'S SIGNATURE. 205 successful runs, head frozen."""
        before = HeadSample(cagg_head=self._T0, last_successful_finish=self._T0)
        after = HeadSample(cagg_head=self._T0, last_successful_finish=self._T1)
        assert _head_advanced(before, after) is False

    def test_head_moved_without_the_job_running_is_false(self) -> None:
        """A manual refresh. Criterion 18 asks whether the POLICY did it."""
        before = HeadSample(cagg_head=self._T0, last_successful_finish=self._T0)
        after = HeadSample(cagg_head=self._T1, last_successful_finish=self._T0)
        assert _head_advanced(before, after) is False

    def test_nothing_happened_is_false(self) -> None:
        before = HeadSample(cagg_head=self._T0, last_successful_finish=self._T0)
        assert _head_advanced(before, before) is False

    def test_first_materialization_counts_as_an_advance(self) -> None:
        """None -> a value is the cagg going from never-materialized to
        materialized, which is an advance rather than an absence of one."""
        before = HeadSample(cagg_head=None, last_successful_finish=self._T0)
        after = HeadSample(cagg_head=self._T0, last_successful_finish=self._T1)
        assert _head_advanced(before, after) is True

    def test_still_empty_after_a_run_is_false(self) -> None:
        before = HeadSample(cagg_head=None, last_successful_finish=self._T0)
        after = HeadSample(cagg_head=None, last_successful_finish=self._T1)
        assert _head_advanced(before, after) is False


class TestPolicyAdvancesHeadUnaided:
    """The mechanism against a real scheduler — no manual refresh, ever."""

    def test_closed_bucket_is_materialized_by_the_policy_alone(
        self, scratch: tuple[str, int]
    ) -> None:
        """Criterion 18's positive case, and D1's central claim.

        Rows land in a bucket that has already closed, and the background
        scheduler — not a manual ``run_job`` or ``refresh_continuous_aggregate``
        — must pick them up.
        """
        url, job_id = scratch

        with psycopg.connect(url, autocommit=True) as conn:
            before = _sample(conn, job_id)
            # 30 days back: comfortably inside a closed bucket at any candidate
            # width, and well inside the policy's start_offset.
            _seed(conn, ago=timedelta(days=30), symbol="ZZCLOSED", n=60)

        after = _wait_for_tick(url, job_id, before, expect_advance=True)

        assert _head_advanced(before, after), (
            "the policy did not advance the head unaided within "
            f"{_TICK_TIMEOUT}: before={before} after={after}"
        )

    def test_open_bucket_is_never_materialized_while_open(
        self, scratch: tuple[str, int]
    ) -> None:
        """The accepted residual, asserted rather than assumed.

        Task C.6a's brief suggested this case should make the head advance.
        Measured on TimescaleDB 2.29.1 it does not: a refresh policy's window
        is truncated to whole buckets, so the open bucket is dropped from every
        refresh — 13 consecutive successful runs materialized nothing.

        Narrowing the width does NOT change this (slice design D1: "nothing
        does"); it bounds how much data the limitation can hide, from up to a
        year down to one bucket width. Asserting the real behaviour here keeps
        the residual documented, and keeps criterion 18's positive test above
        honest about what it is actually proving.
        """
        url, job_id = scratch

        with psycopg.connect(url, autocommit=True) as conn:
            before = _sample(conn, job_id)
            _seed(conn, ago=timedelta(hours=2), symbol="ZZOPEN", n=200)

        after = _wait_for_tick(url, job_id, before, expect_advance=False)

        # The scheduler must have had its chance — otherwise this proves only
        # that the test outran the job.
        assert after.last_successful_finish is not None
        assert (
            before.last_successful_finish is None
            or after.last_successful_finish > before.last_successful_finish
        ), "the policy never ran, so this proves nothing about the open bucket"

        assert not _head_advanced(before, after), (
            "the open bucket was materialized while open — if this now passes, "
            "TimescaleDB's refresh-window truncation changed and slice 169's "
            f"whole premise needs revisiting: before={before} after={after}"
        )

    def test_paused_policy_does_not_advance_the_head(
        self, scratch: tuple[str, int]
    ) -> None:
        """Regression guard: the helper must not be satisfiable by "job exists".

        With the policy paused, a closed-bucket write that WOULD have been
        picked up stays unmaterialized, and ``_head_advanced`` must say so.
        """
        url, job_id = scratch

        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("SELECT alter_job(%s, scheduled => false)", (job_id,))
            before = _sample(conn, job_id)
            _seed(conn, ago=timedelta(days=30), symbol="ZZPAUSED", n=60)

        after = _wait_for_tick(url, job_id, before, expect_advance=False)

        assert not _head_advanced(before, after), (
            f"a paused policy advanced the head: before={before} after={after}"
        )

        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("SELECT alter_job(%s, scheduled => true)", (job_id,))
            row = conn.execute(
                "SELECT scheduled FROM timescaledb_information.jobs WHERE job_id = %s",
                (job_id,),
            ).fetchone()
        assert row is not None and row[0] is True
