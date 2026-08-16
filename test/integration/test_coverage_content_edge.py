"""Integration tests: the coverage content-edge freshness check (slice 187 D6).

Runs against a throwaway database from the ``ephemeral_db`` fixture, following
the slice 167/168 precedent — never a shared database, never a production job.
Requires ``MT_TIMESCALE_TEST_URL``.

What these tests are for. The generic guard buckets the raw edge onto the cagg's
own grid before comparing, so with ``COVERAGE_BUCKET_INTERVAL`` at 365 days no
lag under a year is observable (``cagg_freshness._raw_max``, and the unit-tier
``TestDetectionFloor``). On prod that made ``daily_coverage`` report
``is_fresh=True, lag=0`` while its content sat 52 days behind raw. The
content-edge check compares ``max(last_bucket)`` against the source's
``max(time)`` with no alignment, which is what makes that lag visible.

The stale case below reproduces the production shape exactly: refresh the cagg
so it is genuinely current, then write a newer raw row and do **not** refresh.
That is what a refresh policy whose window never covers the head bucket produces
(D5), and it is asserted here that the bucket check alone still calls it fresh —
otherwise the test would not isolate the new check from the old one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    COVERAGE_CONTENT_STALENESS,
    COVERAGE_SOURCE_TABLE,
    DAILY_COVERAGE_VIEW,
)
from manta_trading.data.maintenance.status_coverage import (
    _content_edge_lag,
    check_coverage_freshness,
)
from manta_trading.market.maintenance.cagg_freshness import (
    StalenessSignal,
    assert_cagg_fresh,
    reset_freshness_cache,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_SYMBOL = "ZZEDGE"

# How far past the coverage edge the stale case writes its raw row.
#
# The scenario only isolates the content-edge check while the lag sits in a
# specific band: **beyond COVERAGE_CONTENT_STALENESS** (so the content check
# fires) but **inside the generic guard's one-bucket detection floor** (so the
# bucket-lag check stays quiet and the control test below means something).
#
# Derived from the constants rather than hardcoded (slice 169). The previous
# literal 10 days satisfied both conditions only at a 365-day bucket; at the
# narrowed 7-day width a 10-day lag is more than one bucket, so the generic
# guard correctly fired and the control test failed — the fixture had drifted
# out of the band, not the code out of spec.
#
# The band is (COVERAGE_CONTENT_STALENESS, 2 x COVERAGE_BUCKET_INTERVAL): above
# the threshold so the content check fires, and close enough to one bucket that
# the bucketed generic lag stays within COVERAGE_BUCKET_LAG_BUDGET. Note the
# generic side is measured in *whole buckets* after alignment, so a raw edge
# only slightly past the threshold can still land two buckets away depending on
# where the seed falls relative to the grid — which is what actually broke the
# literal 10 days at the 7-day width, not the nominal arithmetic.
#
# Sitting just above the threshold keeps the aligned lag at one bucket for any
# grid alignment.
_STALE_LAG = COVERAGE_CONTENT_STALENESS + timedelta(hours=6)


@pytest.fixture
def coverage_db(ephemeral_db: str) -> str:
    """An ephemeral database with the minute/daily schema and coverage caggs."""
    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)
    return ephemeral_db


def _seed_daily(url: str, *, through: datetime, days: int = 30) -> None:
    """Daily bars for the fixture symbol ending at ``through``."""
    rows = [
        (through - timedelta(days=offset), _SYMBOL, 10.0, 10.0, 10.0, 10.0, 100)
        for offset in range(days)
    ]
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO daily_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                rows,
            )
        conn.commit()


def _refresh_daily_coverage(url: str) -> None:
    """Materialize ``daily_coverage`` across a window spanning the present.

    Explicit rather than policy-driven: the policy's own window is what fails to
    cover the head bucket on prod, and these tests need a cagg that is genuinely
    current before staleness is introduced.
    """
    now = datetime.now(tz=UTC)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            f"CALL refresh_continuous_aggregate('{DAILY_COVERAGE_VIEW}', %s, %s)",  # noqa: S608
            (now - 4 * COVERAGE_BUCKET_INTERVAL, now + COVERAGE_BUCKET_INTERVAL),
        )


@pytest.fixture(autouse=True)
def _clear_verdict_cache() -> None:
    """The verdict cache is process-local and keyed by view name only, so a
    verdict from one test would otherwise satisfy the next one's read."""
    reset_freshness_cache()


class TestContentEdgeFresh:
    """Success criterion 4, fresh half: coverage that tracks its source."""

    def test_tracking_coverage_reports_fresh_with_no_content_signal(
        self, coverage_db: str
    ) -> None:
        now = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        _seed_daily(coverage_db, through=now)
        _refresh_daily_coverage(coverage_db)

        with psycopg.connect(coverage_db) as conn:
            freshness = check_coverage_freshness(conn)

        daily = next(
            v for v in freshness.verdicts if v.view_name == DAILY_COVERAGE_VIEW
        )
        assert StalenessSignal.CONTENT_EDGE_TOO_OLD not in daily.signals, daily.detail
        assert daily.is_fresh is True, daily.detail

    def test_content_lag_is_near_zero_when_coverage_is_current(
        self, coverage_db: str
    ) -> None:
        now = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        _seed_daily(coverage_db, through=now)
        _refresh_daily_coverage(coverage_db)

        with psycopg.connect(coverage_db) as conn:
            lag, probe_failed = _content_edge_lag(conn, DAILY_COVERAGE_VIEW)

        assert probe_failed is False
        assert lag == timedelta(0), (
            "a freshly refreshed cagg's last_bucket must equal its source's "
            f"max(time); measured {lag}"
        )


class TestContentEdgeStale:
    """Success criterion 4, stale half — and the isolation that makes it mean
    something: the generic bucket check must still report fresh here."""

    @staticmethod
    def _make_stale(url: str) -> datetime:
        """Refresh coverage, then write a newer raw bar without refreshing.

        Returns the raw edge that results. This is the production defect in
        miniature (D5): the cagg is not broken, it simply stopped being
        re-materialized while raw kept moving.
        """
        now = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        _seed_daily(url, through=now)
        _refresh_daily_coverage(url)
        raw_edge = now + _STALE_LAG
        _seed_daily(url, through=raw_edge, days=1)
        return raw_edge

    def test_bucket_lag_alone_still_reports_fresh(self, coverage_db: str) -> None:
        # The control. If this ever fails, the scenario no longer isolates the
        # content check — the generic guard would be catching the staleness on
        # its own and the assertions below would prove nothing.
        self._make_stale(coverage_db)

        with psycopg.connect(coverage_db) as conn:
            verdict = assert_cagg_fresh(
                conn,
                DAILY_COVERAGE_VIEW,
                source_table=COVERAGE_SOURCE_TABLE[DAILY_COVERAGE_VIEW],
            )

        assert verdict.is_fresh is True, (
            "the one-bucket detection floor must hide a 10-day lag inside a "
            f"365-day bucket; got {verdict.detail}"
        )
        assert StalenessSignal.LAG_EXCEEDS_THRESHOLD not in verdict.signals

    def test_content_edge_check_catches_what_bucket_lag_cannot(
        self, coverage_db: str
    ) -> None:
        self._make_stale(coverage_db)

        with psycopg.connect(coverage_db) as conn:
            freshness = check_coverage_freshness(conn)

        daily = next(
            v for v in freshness.verdicts if v.view_name == DAILY_COVERAGE_VIEW
        )
        assert StalenessSignal.CONTENT_EDGE_TOO_OLD in daily.signals, daily.detail
        assert daily.is_fresh is False
        assert freshness.is_stale is True
        assert daily.lag is not None and daily.lag >= _STALE_LAG
        assert daily.threshold == COVERAGE_CONTENT_STALENESS

    def test_describe_names_the_content_signal(self, coverage_db: str) -> None:
        # describe() joins verdict.signals, so the new member needs no change
        # there — asserted rather than assumed.
        self._make_stale(coverage_db)

        with psycopg.connect(coverage_db) as conn:
            freshness = check_coverage_freshness(conn)

        assert StalenessSignal.CONTENT_EDGE_TOO_OLD.value in freshness.describe()

    def test_measured_lag_matches_the_raw_edge(self, coverage_db: str) -> None:
        raw_edge = self._make_stale(coverage_db)

        with psycopg.connect(coverage_db) as conn:
            lag, probe_failed = _content_edge_lag(conn, DAILY_COVERAGE_VIEW)
            cagg_edge = conn.execute(
                f"SELECT max(last_bucket) FROM {DAILY_COVERAGE_VIEW}"  # noqa: S608
            ).fetchone()

        assert probe_failed is False
        assert cagg_edge is not None and cagg_edge[0] is not None
        assert lag == raw_edge - cagg_edge[0]


class TestContentEdgeCaching:
    """The check runs inside ``assert_cagg_fresh``'s TTL cache (D6), so a repeat
    read must not re-probe — the sub-second NFR slice 167 set depends on it."""

    def test_second_read_inside_the_ttl_issues_no_probes(
        self, coverage_db: str
    ) -> None:
        now = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        _seed_daily(coverage_db, through=now)
        _refresh_daily_coverage(coverage_db)

        with psycopg.connect(coverage_db) as conn:
            check_coverage_freshness(conn)
            # Introduce staleness the cached verdict must not notice: proof the
            # second call did no work, without counting statements through a spy
            # that the pooled real connection would not support.
            _seed_daily(coverage_db, through=now + _STALE_LAG, days=1)
            cached = check_coverage_freshness(conn)

        daily = next(v for v in cached.verdicts if v.view_name == DAILY_COVERAGE_VIEW)
        assert daily.is_fresh is True, (
            "a cached verdict must be returned unchanged within the TTL; "
            f"got {daily.detail}"
        )
