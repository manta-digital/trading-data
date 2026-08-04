"""Unit tests for the guarded data_status accessor (slice 167 D6, review F002).

These assert **propagation**, not re-derivation: the four staleness signals are
slice 168's own tests. What matters here is that a stale verdict reaches the
caller intact, that rows are still returned alongside it (D3a: report, don't
refuse), and that no read path ever remediates.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, cast

import pytest

from manta_trading.constants import (
    COVERAGE_CONTENT_STALENESS,
    COVERAGE_SOURCE_TABLE,
    DAILY_COVERAGE_VIEW,
    MINUTE_COVERAGE_VIEW,
)
from manta_trading.data.maintenance import status_coverage
from manta_trading.data.maintenance.status_coverage import (
    COVERAGE_VIEWS,
    CoverageFreshness,
    check_coverage_freshness,
    query_data_status,
)
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
)


def _fresh(view_name: str) -> FreshnessVerdict:
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=True,
        signals=(),
        lag=timedelta(minutes=3),
        threshold=timedelta(days=1),
        detail=f"{view_name}: fresh",
    )


def _stale(
    view_name: str,
    *signals: StalenessSignal,
    lag: timedelta | None = timedelta(days=40),
) -> FreshnessVerdict:
    fired = signals or (StalenessSignal.LAG_EXCEEDS_THRESHOLD,)
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=False,
        signals=fired,
        lag=lag,
        threshold=timedelta(days=1),
        detail=f"{view_name}: stale",
    )


class _FakeCursor:
    """Minimal psycopg cursor stand-in recording every statement executed."""

    def __init__(self, rows: list[Any], log: list[str]) -> None:
        self._rows = rows
        self._log = log

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self._log.append(sql)

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows if rows is not None else [(7,)]
        self.executed: list[str] = []

    def cursor(self, row_factory: Any = None) -> _FakeCursor:
        return _FakeCursor(self.rows, self.executed)


@pytest.fixture()
def patch_verdicts(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Substitute assert_cagg_fresh with a per-view verdict table."""

    def _install(verdicts: dict[str, FreshnessVerdict]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def _fake(
            conn: Any, view_name: str, **kwargs: Any
        ) -> FreshnessVerdict:
            calls.append({"view_name": view_name, **kwargs})
            return verdicts[view_name]

        monkeypatch.setattr(status_coverage, "assert_cagg_fresh", _fake)
        return calls

    return _install


class TestCheckCoverageFreshness:
    def test_asserts_both_coverage_caggs(self, patch_verdicts) -> None:  # type: ignore[no-untyped-def]
        calls = patch_verdicts({v: _fresh(v) for v in COVERAGE_VIEWS})
        result = check_coverage_freshness(_FakeConn())

        assert [c["view_name"] for c in calls] == list(COVERAGE_VIEWS)
        assert result.is_stale is False

    def test_passes_each_cagg_its_own_source_table(self, patch_verdicts) -> None:  # type: ignore[no-untyped-def]
        """The helper resolves sources from GRANULARITY_SOURCE, which has no
        entry for the coverage caggs -- so the source must be supplied.

        Both are RAW hypertables, including the hierarchical minute cagg: the
        verdict must cover the whole two-hop chain, so a stalled parent cannot
        leave minute_coverage looking fresh while data_status reports months-old
        coverage. (168's _raw_max also probes max(time), which only a raw table
        has.)
        """
        calls = patch_verdicts({v: _fresh(v) for v in COVERAGE_VIEWS})
        check_coverage_freshness(_FakeConn())

        by_view = {c["view_name"]: c["source_table"] for c in calls}
        assert by_view[MINUTE_COVERAGE_VIEW] == "minute_ohlcv"
        assert by_view[DAILY_COVERAGE_VIEW] == "daily_ohlcv"
        assert by_view == COVERAGE_SOURCE_TABLE

    def test_fresh_verdicts_produce_no_error_log(
        self,
        patch_verdicts,  # type: ignore[no-untyped-def]
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        patch_verdicts({v: _fresh(v) for v in COVERAGE_VIEWS})
        with caplog.at_level(logging.ERROR):
            result = check_coverage_freshness(_FakeConn())

        assert result.is_stale is False
        assert caplog.records == []

    @pytest.mark.parametrize(
        "signal",
        [
            StalenessSignal.LAG_EXCEEDS_THRESHOLD,
            StalenessSignal.NOT_SCHEDULED,
            StalenessSignal.LAST_SUCCESS_TOO_OLD,
            StalenessSignal.LAST_RUN_FAILED,
            StalenessSignal.NO_JOB_ROW,
            StalenessSignal.PROBE_FAILED,
        ],
    )
    def test_each_signal_reaches_the_caller(
        self, patch_verdicts, signal: StalenessSignal  # type: ignore[no-untyped-def]
    ) -> None:
        """Propagation, not re-derivation -- the signals are 168's tests."""
        patch_verdicts(
            {
                MINUTE_COVERAGE_VIEW: _stale(MINUTE_COVERAGE_VIEW, signal),
                DAILY_COVERAGE_VIEW: _fresh(DAILY_COVERAGE_VIEW),
            }
        )
        result = check_coverage_freshness(_FakeConn())

        assert result.is_stale is True
        assert signal in result.stale_verdicts[0].signals
        assert signal.value in result.describe()

    def test_either_cagg_alone_makes_coverage_stale(self, patch_verdicts) -> None:  # type: ignore[no-untyped-def]
        """A reader cannot tell which branch a row came from, so one stale cagg
        makes the whole bars_summary untrustworthy."""
        patch_verdicts(
            {
                MINUTE_COVERAGE_VIEW: _fresh(MINUTE_COVERAGE_VIEW),
                DAILY_COVERAGE_VIEW: _stale(DAILY_COVERAGE_VIEW),
            }
        )
        assert check_coverage_freshness(_FakeConn()).is_stale is True

    def test_badly_stalled_cagg_with_loose_offset_still_trips(
        self, patch_verdicts  # type: ignore[no-untyped-def]
    ) -> None:
        """Criterion 7's explicit case.

        The coverage policies carry a deliberately loose 750-day start_offset
        (D4), so an ``start_offset``-relative check alone would pass a cagg
        stalled for months. The helper resolves
        ``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)``; this asserts the
        accessor surfaces that refusal rather than masking it.
        """
        patch_verdicts(
            {
                MINUTE_COVERAGE_VIEW: _stale(
                    MINUTE_COVERAGE_VIEW,
                    StalenessSignal.LAG_EXCEEDS_THRESHOLD,
                    lag=timedelta(days=120),
                ),
                DAILY_COVERAGE_VIEW: _fresh(DAILY_COVERAGE_VIEW),
            }
        )
        result = check_coverage_freshness(_FakeConn())

        assert result.is_stale is True
        assert result.stale_verdicts[0].lag == timedelta(days=120)

    def test_stale_logs_at_error_naming_cagg_and_lag(
        self,
        patch_verdicts,  # type: ignore[no-untyped-def]
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        patch_verdicts(
            {
                MINUTE_COVERAGE_VIEW: _stale(
                    MINUTE_COVERAGE_VIEW, StalenessSignal.NOT_SCHEDULED
                ),
                DAILY_COVERAGE_VIEW: _fresh(DAILY_COVERAGE_VIEW),
            }
        )
        with caplog.at_level(logging.ERROR):
            check_coverage_freshness(_FakeConn())

        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert MINUTE_COVERAGE_VIEW in message
        assert StalenessSignal.NOT_SCHEDULED.value in message
        assert "STALE" in message


class TestQueryDataStatus:
    def test_returns_rows_and_freshness(self, patch_verdicts) -> None:  # type: ignore[no-untyped-def]
        patch_verdicts({v: _fresh(v) for v in COVERAGE_VIEWS})
        conn = _FakeConn(rows=[(1,), (2,)])

        rows, freshness = query_data_status(conn, "SELECT 1 FROM data_status")

        assert rows == [(1,), (2,)]
        assert freshness.is_stale is False

    def test_stale_still_returns_rows_marked_stale(self, patch_verdicts) -> None:  # type: ignore[no-untyped-def]
        """D3a: data_status is operator-facing, so it reports rather than
        refuses. The rows must arrive, and must never be presentable as current
        without the caller seeing is_stale."""
        patch_verdicts(
            {
                MINUTE_COVERAGE_VIEW: _stale(MINUTE_COVERAGE_VIEW),
                DAILY_COVERAGE_VIEW: _fresh(DAILY_COVERAGE_VIEW),
            }
        )
        conn = _FakeConn(rows=[(42,)])

        rows, freshness = query_data_status(conn, "SELECT 1 FROM data_status")

        assert rows == [(42,)]
        assert freshness.is_stale is True

    def test_never_issues_a_refresh(self, patch_verdicts) -> None:  # type: ignore[no-untyped-def]
        """No auto-remediation on a read path (D3a). A status query must not
        trigger a heavy write as a side effect; catch-up stays with runbook R2."""
        patch_verdicts(
            {
                MINUTE_COVERAGE_VIEW: _stale(MINUTE_COVERAGE_VIEW),
                DAILY_COVERAGE_VIEW: _stale(DAILY_COVERAGE_VIEW),
            }
        )
        conn = _FakeConn()

        query_data_status(conn, "SELECT 1 FROM data_status")

        executed = " ".join(conn.executed).lower()
        assert "refresh_continuous_aggregate" not in executed
        assert "call refresh" not in executed

    def test_guard_runs_before_the_query(self, patch_verdicts) -> None:  # type: ignore[no-untyped-def]
        """The verdict must describe the coverage behind the very rows returned,
        so it cannot be evaluated afterwards."""
        order: list[str] = []

        def _fake(conn: Any, view_name: str, **kwargs: Any) -> FreshnessVerdict:
            order.append("guard")
            return _fresh(view_name)

        original = status_coverage.assert_cagg_fresh
        status_coverage.assert_cagg_fresh = _fake  # type: ignore[assignment]
        try:

            class _OrderingConn(_FakeConn):
                def cursor(self, row_factory: Any = None) -> _FakeCursor:
                    order.append("query")
                    return super().cursor(row_factory=row_factory)

            query_data_status(_OrderingConn(), "SELECT 1 FROM data_status")
        finally:
            status_coverage.assert_cagg_fresh = original  # type: ignore[assignment]

        assert order.index("guard") < order.index("query")


class TestCoverageFreshnessDataclass:
    def test_describe_is_quiet_when_fresh(self) -> None:
        freshness = CoverageFreshness(
            verdicts=tuple(_fresh(v) for v in COVERAGE_VIEWS)
        )
        assert freshness.is_stale is False
        assert freshness.stale_verdicts == ()
        assert "fresh" in freshness.describe()

    def test_describe_names_every_stale_cagg(self) -> None:
        freshness = CoverageFreshness(
            verdicts=tuple(_stale(v) for v in COVERAGE_VIEWS)
        )
        described = freshness.describe()
        assert len(freshness.stale_verdicts) == len(COVERAGE_VIEWS)
        for view in COVERAGE_VIEWS:
            assert view in described


_CONN = cast("Any", object())
"""Connection placeholder. Every test below patches ``_content_edge_lag``, which
is the only thing that would touch it, so no database is reachable from here."""


class TestContentEdgeCheck:
    """Slice 187 D6 — the verdict transformation, in isolation from the probes.

    The probes themselves are integration-tested against real caggs
    (``test/integration/test_coverage_content_edge.py``); what is asserted here
    is what the check does with a measured lag, which is pure logic.
    """

    _VIEW = DAILY_COVERAGE_VIEW

    @staticmethod
    def _with_lag(
        monkeypatch: pytest.MonkeyPatch,
        lag: timedelta | None,
        *,
        probe_failed: bool = False,
    ) -> None:
        monkeypatch.setattr(
            status_coverage,
            "_content_edge_lag",
            lambda _conn, _view: (lag, probe_failed),
        )

    def test_lag_beyond_the_threshold_fires_the_signal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._with_lag(monkeypatch, COVERAGE_CONTENT_STALENESS + timedelta(days=51))
        result = status_coverage._apply_content_edge_check(
            _CONN, _fresh(self._VIEW)
        )
        assert result.is_fresh is False
        assert StalenessSignal.CONTENT_EDGE_TOO_OLD in result.signals
        assert result.threshold == COVERAGE_CONTENT_STALENESS

    def test_lag_within_the_threshold_leaves_the_verdict_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A configured end_offset is not staleness — the threshold already
        # budgets for it, so a fresh verdict must survive unchanged.
        self._with_lag(monkeypatch, COVERAGE_CONTENT_STALENESS - timedelta(hours=1))
        verdict = _fresh(self._VIEW)
        assert status_coverage._apply_content_edge_check(_CONN, verdict) is verdict

    def test_exactly_the_threshold_does_not_fire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Boundary: the comparison is `>`, so the budget is inclusive.
        self._with_lag(monkeypatch, COVERAGE_CONTENT_STALENESS)
        verdict = _fresh(self._VIEW)
        assert status_coverage._apply_content_edge_check(_CONN, verdict) is verdict

    def test_unmeasurable_lag_leaves_the_verdict_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty source or cagg is an absence of data, not staleness.
        self._with_lag(monkeypatch, None)
        verdict = _fresh(self._VIEW)
        assert status_coverage._apply_content_edge_check(_CONN, verdict) is verdict

    def test_signal_is_appended_to_existing_ones_not_replacing_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Collect-all-signals: an operator needs every reason at once.
        self._with_lag(monkeypatch, COVERAGE_CONTENT_STALENESS + timedelta(days=10))
        result = status_coverage._apply_content_edge_check(
            _CONN, _stale(self._VIEW, StalenessSignal.NOT_SCHEDULED)
        )
        assert StalenessSignal.NOT_SCHEDULED in result.signals
        assert StalenessSignal.CONTENT_EDGE_TOO_OLD in result.signals

    def test_content_probe_failure_makes_the_verdict_stale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review F003 — the case that would otherwise report fresh silently.

        The generic probes succeeded, so nothing else in the verdict records
        that this check was attempted. Returning "no lag" would report fresh on
        a check that never ran: the vacuous verdict this slice exists to
        eliminate, reintroduced through the error path.
        """
        self._with_lag(monkeypatch, None, probe_failed=True)
        result = status_coverage._apply_content_edge_check(
            _CONN, _fresh(self._VIEW)
        )
        assert result.is_fresh is False
        assert StalenessSignal.CONTENT_EDGE_PROBE_FAILED in result.signals
        assert StalenessSignal.CONTENT_EDGE_TOO_OLD not in result.signals

    def test_content_probe_failure_is_distinct_from_a_measured_zero_lag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A measured lag of None (empty table) is NOT staleness; a failed probe
        # is. Same `lag is None`, opposite verdicts — which is exactly why the
        # helper returns a flag rather than overloading None.
        self._with_lag(monkeypatch, None, probe_failed=False)
        verdict = _fresh(self._VIEW)
        assert status_coverage._apply_content_edge_check(_CONN, verdict) is verdict

    def test_probe_failed_verdict_skips_the_content_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The generic evaluation could not read the connection at all. Probing
        # it twice more cannot help, and the verdict is already stale on the
        # strongest grounds (168 D3). Regression guard: without this skip, the
        # health route's cancelled-probe path computes a lag from a connection
        # that just failed.
        def _must_not_probe(_conn: Any, _view: str) -> tuple[timedelta, bool]:
            raise AssertionError("content-edge probe ran after PROBE_FAILED")

        monkeypatch.setattr(status_coverage, "_content_edge_lag", _must_not_probe)
        verdict = _stale(self._VIEW, StalenessSignal.PROBE_FAILED, lag=None)
        assert status_coverage._apply_content_edge_check(_CONN, verdict) is verdict
