"""Unit tests for the coverage-cagg rebuild sweep (slice 169 Task G).

Covers the parts that must be right *before* the sweep is pointed at a 139 GB
production database: sub-window planning (bucket alignment, bounded span,
coverage of the whole range) and the two pre-flight refusals, which are the only
things standing between an operator and the slice-163 corruption shape.

The DB-touching sweep itself is exercised at the integration tier.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    COVERAGE_BUCKET_ORIGIN,
    COVERAGE_REFRESH_MIN_WINDOW_BUCKETS,
    DAILY_COVERAGE_VIEW,
    GRANULARITY_SOURCE,
    MINUTE_COVERAGE_VIEW,
    Granularity,
)
from manta_trading.market.maintenance.coverage_rebuild import (
    FAMILY_SOURCE,
    FAMILY_VIEW,
    REBUILD_SUBWINDOW,
    CoverageFamily,
    plan_windows,
)


class TestFamilyMapping:
    """The two dispatch maps must key off constants, never spelled-out names."""

    def test_every_family_has_a_view_and_a_source(self) -> None:
        for family in CoverageFamily:
            assert family in FAMILY_VIEW
            assert family in FAMILY_SOURCE

    def test_views_come_from_the_constants(self) -> None:
        assert FAMILY_VIEW[CoverageFamily.DAILY] == DAILY_COVERAGE_VIEW
        assert FAMILY_VIEW[CoverageFamily.MINUTE] == MINUTE_COVERAGE_VIEW

    def test_minute_sweeps_its_parent_cagg_not_raw(self) -> None:
        """``minute_coverage`` is hierarchical, so the span it can materialize
        is the 4-hour parent's, not raw ``minute_ohlcv``'s.

        Sweeping to raw's edge would issue refreshes over a range the parent
        has not filled — materializing nothing and understating the trailing
        edge. This is the one place FAMILY_SOURCE deliberately differs from
        COVERAGE_SOURCE_TABLE, which maps to raw for freshness purposes.
        """
        assert (
            FAMILY_SOURCE[CoverageFamily.MINUTE] == GRANULARITY_SOURCE[Granularity.H4]
        )
        assert FAMILY_SOURCE[CoverageFamily.MINUTE] != "minute_ohlcv"

    def test_daily_sweeps_raw(self) -> None:
        """``daily_coverage`` reads the raw hypertable directly — no parent."""
        assert FAMILY_SOURCE[CoverageFamily.DAILY] == "daily_ohlcv"


class TestPlanWindows:
    _START = datetime(2020, 1, 1, tzinfo=UTC)

    def test_empty_when_end_precedes_start(self) -> None:
        assert plan_windows(self._START, self._START) == []
        assert plan_windows(self._START, self._START - timedelta(days=1)) == []

    def test_windows_are_contiguous_and_ordered(self) -> None:
        windows = plan_windows(self._START, self._START + timedelta(days=1000))
        assert windows == sorted(windows, key=lambda w: w.start)
        for earlier, later in zip(windows, windows[1:], strict=False):
            assert earlier.end == later.start, "sub-windows must not gap or overlap"

    def test_windows_cover_the_requested_span(self) -> None:
        end = self._START + timedelta(days=1000)
        windows = plan_windows(self._START, end)
        assert windows[0].start <= self._START
        assert windows[-1].end >= end

    def test_no_window_exceeds_the_subwindow_span(self) -> None:
        """The bound is the whole point: a refresh materializes its range as one
        in-memory tuplestore outside work_mem's control."""
        span = timedelta(days=365)
        for window in plan_windows(
            self._START, self._START + timedelta(days=5000), span
        ):
            assert window.end - window.start <= span

    @pytest.mark.parametrize("days", [7, 30, 365, 3650, 23600])
    def test_boundaries_land_on_bucket_edges(self, days: int) -> None:
        """CRITICAL. A refresh only materializes buckets *fully contained* in
        its range, so a sub-window boundary falling mid-bucket would leave that
        bucket unwritten by both adjacent calls — reintroducing, through the
        sweep, the exact truncation behaviour this slice exists to repair.
        """
        windows = plan_windows(self._START, self._START + timedelta(days=days))
        bucket_s = COVERAGE_BUCKET_INTERVAL.total_seconds()
        # The engine's grid, not calendar midnight-2000: see the constant.
        epoch = COVERAGE_BUCKET_ORIGIN
        for window in windows:
            for edge in (window.start, window.end):
                offset = (edge - epoch).total_seconds()
                assert offset % bucket_s == 0, (
                    f"{edge} is not on the {COVERAGE_BUCKET_INTERVAL} bucket grid"
                )

    def test_subwindow_is_rounded_down_to_whole_buckets(self) -> None:
        """A sub-window span that is not a bucket multiple would push every
        later boundary off the grid."""
        odd = COVERAGE_BUCKET_INTERVAL * 3 + timedelta(hours=5)
        windows = plan_windows(self._START, self._START + timedelta(days=400), odd)
        for window in windows[:-1]:
            span = window.end - window.start
            assert span.total_seconds() % COVERAGE_BUCKET_INTERVAL.total_seconds() == 0

    def test_span_narrower_than_a_bucket_still_yields_whole_buckets(self) -> None:
        """A caller passing an over-narrow --subwindow-days must not produce
        zero-width windows and spin forever — and every window must satisfy
        the engine floor (``refresh window too small`` below 2 buckets)."""
        floor = COVERAGE_REFRESH_MIN_WINDOW_BUCKETS * COVERAGE_BUCKET_INTERVAL
        windows = plan_windows(
            self._START, self._START + timedelta(days=30), timedelta(hours=1)
        )
        assert windows
        for window in windows:
            assert window.end - window.start >= floor

    def test_no_window_is_narrower_than_the_engine_floor(self) -> None:
        """The trailing remainder must be absorbed, not emitted: TimescaleDB
        rejects any refresh narrower than 2 buckets, so a 1-bucket tail would
        fail the sweep on its last call."""
        floor = COVERAGE_REFRESH_MIN_WINDOW_BUCKETS * COVERAGE_BUCKET_INTERVAL
        # A grid-aligned start (so snapping cannot widen the span): 13 buckets
        # in sub-windows of 3 leaves a 1-bucket remainder.
        aligned = COVERAGE_BUCKET_ORIGIN + COVERAGE_BUCKET_INTERVAL * 1043
        windows = plan_windows(
            aligned,
            aligned + COVERAGE_BUCKET_INTERVAL * 13,
            COVERAGE_BUCKET_INTERVAL * 3,
        )
        assert len(windows) == 4, "the remainder must merge into the last window"
        assert all(w.end - w.start >= floor for w in windows)
        assert windows[-1].end - windows[-1].start == COVERAGE_BUCKET_INTERVAL * 4

    def test_default_subwindow_is_bounded(self) -> None:
        """Regression guard: the default must never become "the whole span"."""
        assert REBUILD_SUBWINDOW > timedelta(0)
        assert REBUILD_SUBWINDOW <= timedelta(days=365)

    def test_sixty_four_years_produces_a_manageable_window_count(self) -> None:
        """Prod's daily span is 1962..2026. The sweep must be a few dozen calls,
        not thousands (which would make the run unmonitorable) nor one (which
        is the unbounded allocation)."""
        windows = plan_windows(
            datetime(1962, 1, 1, tzinfo=UTC), datetime(2026, 8, 13, tzinfo=UTC)
        )
        assert 40 <= len(windows) <= 120


class _FakeConn:
    """Minimal connection stub: answers the job-catalog query only."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.executed: list[str] = []

    def execute(self, sql: str, params: object = None):  # noqa: ANN201
        self.executed.append(sql)
        return self

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


def _job(job_id: int, proc: str, view: str, *, scheduled: bool) -> dict[str, object]:
    return {
        "job_id": job_id,
        "proc_name": proc,
        "hypertable_name": view,
        "scheduled": scheduled,
    }


# Imported, not restated: these are the names `_resolve_cagg_jobs` filters the
# job catalog on, so a local literal could drift out of agreement with the code
# under test and quietly make every refusal test vacuous.
from manta_trading.market.maintenance.cagg_repair import (  # noqa: E402
    _PROC_COLUMNSTORE as _COLUMNSTORE,
)
from manta_trading.market.maintenance.cagg_repair import (  # noqa: E402
    _PROC_REFRESH as _REFRESH,
)


class TestPolicyPausedRefusal:
    """The guard against the slice-163 corruption shape."""

    def test_refuses_when_the_refresh_policy_is_live(self) -> None:
        from manta_trading.market.maintenance.coverage_rebuild import (
            assert_policies_paused,
        )
        from manta_trading.market.maintenance.rechunk import PreflightError

        conn = _FakeConn([_job(1108, _REFRESH, DAILY_COVERAGE_VIEW, scheduled=True)])
        with pytest.raises(PreflightError) as excinfo:
            assert_policies_paused(conn, DAILY_COVERAGE_VIEW)  # type: ignore[arg-type]

        message = str(excinfo.value)
        assert "1108" in message, "the refusal must name the actual job id"
        assert "alter_job(1108, scheduled => false)" in message, (
            "the refusal must print the exact command, so the operator does "
            "not have to guess it"
        )

    def test_passes_when_every_policy_is_paused(self) -> None:
        from manta_trading.market.maintenance.coverage_rebuild import (
            assert_policies_paused,
        )

        conn = _FakeConn(
            [
                _job(1108, _REFRESH, DAILY_COVERAGE_VIEW, scheduled=False),
                _job(1109, _COLUMNSTORE, DAILY_COVERAGE_VIEW, scheduled=False),
            ]
        )
        assert_policies_paused(conn, DAILY_COVERAGE_VIEW)  # type: ignore[arg-type]

    def test_refuses_when_only_the_columnstore_policy_is_live(self) -> None:
        """Both matter. A columnstore job compressing chunks mid-sweep is the
        same collision class as a refresh."""
        from manta_trading.market.maintenance.coverage_rebuild import (
            assert_policies_paused,
        )
        from manta_trading.market.maintenance.rechunk import PreflightError

        conn = _FakeConn(
            [
                _job(1108, _REFRESH, DAILY_COVERAGE_VIEW, scheduled=False),
                _job(1109, _COLUMNSTORE, DAILY_COVERAGE_VIEW, scheduled=True),
            ]
        )
        with pytest.raises(PreflightError, match="1109"):
            assert_policies_paused(conn, DAILY_COVERAGE_VIEW)  # type: ignore[arg-type]

    def test_no_jobs_at_all_is_allowed(self) -> None:
        """A freshly recreated cagg has no policies until 052 installs them —
        that is the normal state mid-rebuild, not a refusal."""
        from manta_trading.market.maintenance.coverage_rebuild import (
            assert_policies_paused,
        )

        assert_policies_paused(_FakeConn([]), DAILY_COVERAGE_VIEW)  # type: ignore[arg-type]


class TestCoverageIndexRefusal:
    """Opposite polarity: the PARENT must stay scheduled."""

    def test_refuses_when_the_parent_refresh_is_paused(self) -> None:
        """Pausing minute_4hour_ohlcv makes the daemon re-seed and re-pull
        recent sessions every cycle (prod incident 2026-07-25), and it is also
        this sweep's source."""
        from manta_trading.market.maintenance.coverage_rebuild import (
            assert_coverage_index_scheduled,
        )
        from manta_trading.market.maintenance.rechunk import PreflightError

        parent = GRANULARITY_SOURCE[Granularity.H4]
        conn = _FakeConn([_job(1124, _REFRESH, parent, scheduled=False)])
        with pytest.raises(PreflightError) as excinfo:
            assert_coverage_index_scheduled(conn)  # type: ignore[arg-type]

        message = str(excinfo.value)
        assert "1124" in message
        assert "alter_job(1124, scheduled => true)" in message

    def test_passes_when_the_parent_is_scheduled(self) -> None:
        from manta_trading.market.maintenance.coverage_rebuild import (
            assert_coverage_index_scheduled,
        )

        parent = GRANULARITY_SOURCE[Granularity.H4]
        conn = _FakeConn([_job(1124, _REFRESH, parent, scheduled=True)])
        assert_coverage_index_scheduled(conn)  # type: ignore[arg-type]
