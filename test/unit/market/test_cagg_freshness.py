"""Unit tests for the slice 168 cagg freshness assertion.

Covers the verdict type and signal enum (task 2), the job-catalog read (task 3),
the edge probes, their timeout discipline, and threshold resolution (task 4),
the four-signal evaluation plus indeterminate handling (task 5), and the TTL
verdict cache (task 6). The DB is faked at the ``execute()`` boundary;
assertions are on call *order* and bound parameters, not SQL text.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Any, Literal

import psycopg
import pytest

from manta_trading.constants import (
    CAGG_FRESHNESS_CACHE_TTL,
    CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT,
    MAX_COVERAGE_SOURCE_STALENESS,
)
from manta_trading.market.maintenance import cagg_freshness
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
    _cagg_max,
    _evaluate,
    _raw_max,
    _read_refresh_job,
    _resolve_source_table,
    _resolve_threshold,
    assert_cagg_fresh,
    reset_freshness_cache,
)

_VIEW = "minute_4hour_ohlcv"
_RAW = "minute_ohlcv"
_DAILY_VIEW = "daily_quarterly_ohlcv"


def _utc(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


class _RecordingCursor:
    """Cursor fake that records (sql, params) in execution order."""

    def __init__(self, log: list[tuple[str, object]], rows: list[Any]) -> None:
        self._log = log
        self._rows = rows

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self._log.append((sql, params))

    def fetchone(self) -> Any:
        return self._rows.pop(0) if self._rows else None


class _RecordingConnection:
    """Connection fake handing out cursors that share one execution log."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.log: list[tuple[str, object]] = []
        self._rows = rows if rows is not None else []

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self.log, self._rows)

    @property
    def statements(self) -> list[str]:
        return [sql for sql, _ in self.log]


class TestStalenessSignal:
    """The enum is the dispatch vocabulary; adding a member without test
    coverage should break the suite."""

    def test_has_exactly_the_six_expected_members(self) -> None:
        assert {member.value for member in StalenessSignal} == {
            "LAG_EXCEEDS_THRESHOLD",
            "NOT_SCHEDULED",
            "LAST_SUCCESS_TOO_OLD",
            "LAST_RUN_FAILED",
            "NO_JOB_ROW",
            "PROBE_FAILED",
        }

    def test_members_are_strings(self) -> None:
        # StrEnum so log formatting and comparison never need .value juggling.
        assert StalenessSignal.NOT_SCHEDULED == "NOT_SCHEDULED"


class TestFreshnessVerdict:
    def test_fresh_verdict_has_no_signals(self) -> None:
        verdict = FreshnessVerdict(
            view_name=_VIEW,
            is_fresh=True,
            signals=(),
            lag=timedelta(minutes=5),
            threshold=timedelta(days=1),
            detail="fresh",
        )
        assert verdict.is_fresh is True
        assert verdict.signals == ()

    def test_stale_verdict_carries_every_signal_that_fired(self) -> None:
        # Not just the first: the ERROR log names all of them.
        signals = (
            StalenessSignal.LAG_EXCEEDS_THRESHOLD,
            StalenessSignal.NOT_SCHEDULED,
            StalenessSignal.LAST_RUN_FAILED,
        )
        verdict = FreshnessVerdict(
            view_name=_VIEW,
            is_fresh=False,
            signals=signals,
            lag=timedelta(days=4),
            threshold=timedelta(days=1),
            detail="stale",
        )
        assert verdict.is_fresh is False
        assert verdict.signals == signals

    def test_verdict_is_frozen(self) -> None:
        verdict = FreshnessVerdict(
            view_name=_VIEW,
            is_fresh=True,
            signals=(),
            lag=None,
            threshold=None,
            detail="",
        )
        try:
            verdict.is_fresh = False  # type: ignore[misc]
        except AttributeError:
            return
        raise AssertionError("FreshnessVerdict must be immutable")


class TestReadRefreshJob:
    """Task 3.2 — the catalog read, against fakes."""

    def test_populated_row_parses_with_correct_types(self) -> None:
        conn = _RecordingConnection(
            rows=[(1003, True, timedelta(days=1), "Success", _utc(2026, 7, 26))]
        )
        job = _read_refresh_job(conn, _VIEW)  # type: ignore[arg-type]
        assert job is not None
        assert job.job_id == 1003
        assert job.scheduled is True
        assert job.start_offset == timedelta(days=1)
        assert job.last_run_status == "Success"
        assert job.last_successful_finish == _utc(2026, 7, 26)

    def test_empty_result_returns_none(self) -> None:
        conn = _RecordingConnection(rows=[])
        assert _read_refresh_job(conn, _VIEW) is None  # type: ignore[arg-type]

    def test_view_name_is_a_bound_parameter_not_inlined(self) -> None:
        conn = _RecordingConnection(rows=[(1, True, None, "Success", None)])
        _read_refresh_job(conn, _VIEW)  # type: ignore[arg-type]
        catalog_sql, params = conn.log[-1]
        assert _VIEW not in catalog_sql, "view_name must not be interpolated"
        assert isinstance(params, tuple)
        assert params[0] == _VIEW

    def test_start_offset_is_cast_to_interval_in_sql(self) -> None:
        # TimescaleDB keeps start_offset in jobs.config as a jsonb interval
        # string; the cast is what makes psycopg return a timedelta.
        conn = _RecordingConnection(
            rows=[(1, True, timedelta(days=1), "Success", None)]
        )
        _read_refresh_job(conn, _VIEW)  # type: ignore[arg-type]
        catalog_sql = conn.log[-1][0]
        assert "'start_offset'" in catalog_sql
        assert "::interval" in catalog_sql


class TestProbeTimeoutDiscipline:
    """Task 4.1a — the bound that converts a hang into a refusal is actually
    configured, on every path. Missing ``statement_timeout`` on probe queries is
    the root-cause class of the 2026-07-20 prod incident."""

    @pytest.mark.parametrize("probe_returns_row", [True, False])
    def test_cagg_probe_sets_timeout_before_the_max_query(
        self, probe_returns_row: bool
    ) -> None:
        # Parametrized over the row/None early-return paths: the timeout must
        # precede the probe on both.
        rows: list[Any] = [(_utc(2026, 7, 24),)] if probe_returns_row else []
        conn = _RecordingConnection(rows=rows)
        _cagg_max(conn, _VIEW)  # type: ignore[arg-type]
        statements = conn.statements
        assert len(statements) == 2
        assert "statement_timeout" in statements[0]
        assert statements[1].startswith("SELECT max(")
        assert "statement_timeout" not in statements[1]

    @pytest.mark.parametrize("probe_returns_row", [True, False])
    def test_raw_probe_sets_timeout_before_the_max_query(
        self, probe_returns_row: bool
    ) -> None:
        rows: list[Any] = [(_utc(2026, 7, 24),)] if probe_returns_row else []
        conn = _RecordingConnection(rows=rows)
        _raw_max(conn, _RAW)  # type: ignore[arg-type]
        statements = conn.statements
        assert "statement_timeout" in statements[0]
        assert statements[1].startswith("SELECT max(")

    def test_catalog_read_also_sets_the_timeout_first(self) -> None:
        conn = _RecordingConnection(rows=[(1, True, None, "Success", None)])
        _read_refresh_job(conn, _VIEW)  # type: ignore[arg-type]
        assert "statement_timeout" in conn.statements[0]

    def test_timeout_value_comes_from_the_constant_not_a_literal(self) -> None:
        conn = _RecordingConnection(rows=[(_utc(2026, 7, 24),)])
        _cagg_max(conn, _VIEW)  # type: ignore[arg-type]
        assert CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT in conn.statements[0]

    def test_probe_relation_resolves_through_granularity_source(self) -> None:
        # The raw table reached by the probe is derived from GRANULARITY_SOURCE,
        # never from string manipulation of the caller's view name.
        assert _resolve_source_table(_VIEW) == _RAW
        assert _resolve_source_table(_DAILY_VIEW) == "daily_ohlcv"

    def test_unknown_view_name_raises_valueerror(self) -> None:
        # A caller bug, not a staleness condition (F001) — must not be absorbed.
        with pytest.raises(ValueError, match="not a known continuous aggregate"):
            _resolve_source_table("not_a_cagg")

    def test_base_hypertable_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="base hypertable"):
            _resolve_source_table(_RAW)


class TestResolveThreshold:
    """Task 4.3 — threshold resolution, including the 270-day regression."""

    @pytest.mark.parametrize(
        "start_offset",
        [
            timedelta(hours=1),
            timedelta(days=1),
            timedelta(days=21),
            timedelta(days=90),
            timedelta(days=270),
        ],
    )
    def test_resolves_to_min_of_offset_and_ceiling(
        self, start_offset: timedelta
    ) -> None:
        assert _resolve_threshold(start_offset) == min(
            start_offset, MAX_COVERAGE_SOURCE_STALENESS
        )

    def test_none_offset_falls_back_to_the_ceiling(self) -> None:
        assert _resolve_threshold(None) == MAX_COVERAGE_SOURCE_STALENESS

    def test_daily_cagg_stalled_100_days_is_stale_despite_270_day_offset(
        self,
    ) -> None:
        # REGRESSION for design criterion 3. The daily_quarterly_ohlcv refresh
        # policy really does use start_offset = 270 days (verified on prod), so
        # without the min() ceiling a 100-day stall passes every
        # start_offset-relative check. THIS TEST MUST FAIL if the ceiling is
        # removed from _resolve_threshold.
        threshold = _resolve_threshold(timedelta(days=270))
        assert timedelta(days=100) > threshold, (
            "a 100-day stall must exceed the threshold; removing the "
            "MAX_COVERAGE_SOURCE_STALENESS ceiling breaks this"
        )


# --- Task 5: evaluation fixtures -------------------------------------------
#
# _evaluate issues exactly three queries in order: catalog read, cagg edge,
# raw edge. The fake below serves that sequence and can raise on any of them.

_NOW = _utc(2026, 7, 26)
_CAGG_EDGE = _NOW - timedelta(hours=2)
_RAW_EDGE = _NOW - timedelta(minutes=30)


class _EvalConnection(_RecordingConnection):
    """Serves the catalog row then the two edge probes, optionally raising."""

    def __init__(
        self,
        *,
        scheduled: bool = True,
        start_offset: timedelta | None = timedelta(days=1),
        last_run_status: str | None = "Success",
        last_successful_finish: datetime | None = None,
        job_row: bool = True,
        cagg_max: datetime | None = _CAGG_EDGE,
        raw_max: datetime | None = _RAW_EDGE,
        raise_on_query: int | None = None,
    ) -> None:
        catalog: Any = (
            (
                1003,
                scheduled,
                start_offset,
                last_run_status,
                last_successful_finish if last_successful_finish else _NOW,
            )
            if job_row
            else None
        )
        self._template: list[Any] = [
            catalog,
            (cagg_max,) if cagg_max is not None else None,
            (raw_max,) if raw_max is not None else None,
        ]
        super().__init__(rows=list(self._template))
        self._raise_on_query = raise_on_query
        self._query_count = 0

    def reload(self) -> None:
        """Refill the row queue for a second evaluation (TTL-expiry test)."""
        self._rows[:] = list(self._template)

    def cursor(self) -> _RecordingCursor:
        return _RaisingCursor(self)


class _RaisingCursor(_RecordingCursor):
    """Counts non-timeout statements; raises psycopg.OperationalError on the
    configured one, which is how a probe timeout arrives in practice."""

    def __init__(self, conn: _EvalConnection) -> None:
        super().__init__(conn.log, conn._rows)
        self._conn = conn

    def execute(self, sql: str, params: object = None) -> None:
        super().execute(sql, params)
        if "statement_timeout" in sql:
            return
        self._conn._query_count += 1
        if self._conn._query_count == self._conn._raise_on_query:
            raise psycopg.OperationalError("canceling statement due to timeout")


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze _evaluate's wall clock so LAST_SUCCESS_TOO_OLD is deterministic."""
    monkeypatch.setattr(cagg_freshness, "_now", lambda: _NOW)


class TestEvaluateSignals:
    """Task 5.3 — each D1 signal in isolation, the other three healthy."""

    def test_healthy_cagg_is_fresh(self) -> None:
        # Criterion 4: no false positive on a healthy cagg.
        verdict = _evaluate(_EvalConnection(), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is True
        assert verdict.signals == ()
        assert verdict.lag == _RAW_EDGE - _CAGG_EDGE

    def test_lag_exceeding_threshold_trips(self) -> None:
        # Raw ran four days past the cagg edge — the 163 incident's lag shape.
        verdict = _evaluate(  # type: ignore[arg-type]
            _EvalConnection(cagg_max=_NOW - timedelta(days=4)), _VIEW
        )
        assert verdict.is_fresh is False
        assert StalenessSignal.LAG_EXCEEDS_THRESHOLD in verdict.signals

    def test_paused_policy_trips(self) -> None:
        # The exact 163 incident: job 1003 left scheduled=false.
        verdict = _evaluate(_EvalConnection(scheduled=False), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert StalenessSignal.NOT_SCHEDULED in verdict.signals

    def test_stale_last_success_trips(self) -> None:
        verdict = _evaluate(  # type: ignore[arg-type]
            _EvalConnection(last_successful_finish=_NOW - timedelta(days=5)), _VIEW
        )
        assert verdict.is_fresh is False
        assert StalenessSignal.LAST_SUCCESS_TOO_OLD in verdict.signals

    def test_failing_last_run_trips(self) -> None:
        # Scheduled and firing, but failing every time — a scheduled-only check
        # reports this as healthy.
        verdict = _evaluate(_EvalConnection(last_run_status="Failed"), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert StalenessSignal.LAST_RUN_FAILED in verdict.signals

    def test_every_signal_that_fired_is_collected(self) -> None:
        verdict = _evaluate(  # type: ignore[arg-type]
            _EvalConnection(
                scheduled=False,
                last_run_status="Failed",
                cagg_max=_NOW - timedelta(days=4),
                last_successful_finish=_NOW - timedelta(days=5),
            ),
            _VIEW,
        )
        assert set(verdict.signals) == {
            StalenessSignal.LAG_EXCEEDS_THRESHOLD,
            StalenessSignal.NOT_SCHEDULED,
            StalenessSignal.LAST_SUCCESS_TOO_OLD,
            StalenessSignal.LAST_RUN_FAILED,
        }


class TestEvaluateIndeterminate:
    """Task 5.2/5.3 — F001 indeterminate handling."""

    def test_missing_job_row_trips(self) -> None:
        # A cagg with no refresh policy never self-heals.
        verdict = _evaluate(_EvalConnection(job_row=False), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert verdict.signals == (StalenessSignal.NO_JOB_ROW,)

    def test_non_cagg_view_name_raises_valueerror(self) -> None:
        # A caller bug must not be absorbed into a staleness refusal.
        with pytest.raises(ValueError, match="not a known continuous aggregate"):
            _evaluate(_EvalConnection(), "not_a_cagg")  # type: ignore[arg-type]

    @pytest.mark.parametrize("failing_query", [1, 2, 3])
    def test_probe_error_trips_with_probe_failed(self, failing_query: int) -> None:
        # Whichever of the three queries raises, the verdict is a refusal and
        # the error never propagates into the reader's own error path.
        verdict = _evaluate(  # type: ignore[arg-type]
            _EvalConnection(raise_on_query=failing_query), _VIEW
        )
        assert verdict.is_fresh is False
        assert verdict.signals == (StalenessSignal.PROBE_FAILED,)
        assert verdict.lag is None


class TestVerdictCache:
    """Task 6.2 / design criterion 8 — the TTL cache, both directions.

    Asserted by **query count** on a counting fake, never by timing.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> Iterator[None]:
        reset_freshness_cache()
        yield
        reset_freshness_cache()

    @staticmethod
    def _probe_count(conn: _EvalConnection) -> int:
        return len([sql for sql in conn.statements if "statement_timeout" not in sql])

    def test_warm_call_issues_no_probe_queries(self) -> None:
        conn = _EvalConnection()
        first = assert_cagg_fresh(conn, _VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        after_cold = self._probe_count(conn)
        second = assert_cagg_fresh(conn, _VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        assert self._probe_count(conn) == after_cold, (
            "a warm call must issue zero probe queries"
        )
        assert second == first

    def test_cached_stale_verdict_still_refuses(self) -> None:
        # The cache must never convert a refusal into a pass.
        conn = _EvalConnection(scheduled=False)
        first = assert_cagg_fresh(conn, _VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        second = assert_cagg_fresh(conn, _VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        assert first.is_fresh is False
        assert second.is_fresh is False
        assert StalenessSignal.NOT_SCHEDULED in second.signals

    def test_advancing_past_the_ttl_re_probes(self) -> None:
        conn = _EvalConnection()
        assert_cagg_fresh(conn, _VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        after_cold = self._probe_count(conn)
        # Refill the fake's row queue for the second evaluation.
        conn.reload()
        expired = _NOW + CAGG_FRESHNESS_CACHE_TTL + timedelta(seconds=1)
        assert_cagg_fresh(conn, _VIEW, now=lambda: expired)  # type: ignore[arg-type]
        assert self._probe_count(conn) == after_cold * 2, (
            "an expired entry must trigger a full re-probe"
        )

    def test_distinct_view_names_do_not_share_an_entry(self) -> None:
        stale_conn = _EvalConnection(scheduled=False)
        fresh_conn = _EvalConnection()
        stale = assert_cagg_fresh(stale_conn, _VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        fresh = assert_cagg_fresh(fresh_conn, _DAILY_VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        assert stale.is_fresh is False
        assert fresh.is_fresh is True
        assert fresh.view_name == _DAILY_VIEW
