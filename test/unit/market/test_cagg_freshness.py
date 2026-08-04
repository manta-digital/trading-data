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
from typing import Any, Literal, cast

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
    _restore_probe_timeout,
    assert_cagg_fresh,
    reset_freshness_cache,
)

_VIEW = "minute_4hour_ohlcv"
_RAW = "minute_ohlcv"
_DAILY_VIEW = "daily_quarterly_ohlcv"


def _utc(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _render_sql(query: object) -> str:
    """Render a psycopg ``sql.Composed``/``sql.SQL`` to text, connectionless.

    Identifier-composed probes quote their identifiers, so the rendered text
    reads ``FROM "minute_ohlcv"`` rather than ``FROM minute_ohlcv``; assertions
    on relation names accommodate that by matching the quoted form.
    """
    return cast("Any", query).as_string(None)


class _RecordingCursor:
    """Cursor fake that records (sql, params) in execution order.

    ``SHOW statement_timeout`` is answered from the connection's tracked GUC
    rather than from the scripted row queue: it is bookkeeping the module does
    around the probes, not one of the probes a test is scripting, and letting
    it consume a queued row would silently shift every subsequent fetch.
    """

    def __init__(
        self, log: list[tuple[str, object]], rows: list[Any], conn: _RecordingConnection
    ) -> None:
        self._log = log
        self._rows = rows
        self._conn = conn
        self._show_pending = False

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False

    def execute(self, query: object, params: object = None) -> None:
        # Identifier-composed probes arrive as psycopg sql.Composed, not str
        # (review F001). Render to text so assertions on SQL content — and the
        # bookkeeping below — work uniformly across both forms.
        text = query if isinstance(query, str) else _render_sql(query)
        self._log.append((text, params))
        normalized = text.strip().lower()
        self._show_pending = normalized == "show statement_timeout"
        if normalized.startswith("set statement_timeout"):
            self._conn.statement_timeout = text.split("=", 1)[1].strip().strip("'")

    def fetchone(self) -> Any:
        if self._show_pending:
            self._show_pending = False
            return (self._conn.statement_timeout,)
        return self._rows.pop(0) if self._rows else None


class _RecordingConnection:
    """Connection fake handing out cursors that share one execution log."""

    # The session GUC the module reads before probing and restores after.
    # A caller-set value, so the default is deliberately not the probe timeout.
    _INITIAL_STATEMENT_TIMEOUT = "0"

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.log: list[tuple[str, object]] = []
        self._rows = rows if rows is not None else []
        self.statement_timeout = self._INITIAL_STATEMENT_TIMEOUT

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self.log, self._rows, self)

    @property
    def statements(self) -> list[str]:
        return [sql for sql, _ in self.log]


class TestStalenessSignal:
    """The enum is the dispatch vocabulary; adding a member without test
    coverage should break the suite."""

    def test_has_exactly_the_eight_expected_members(self) -> None:
        assert {member.value for member in StalenessSignal} == {
            "LAG_EXCEEDS_THRESHOLD",
            "NOT_SCHEDULED",
            "LAST_SUCCESS_TOO_OLD",
            "LAST_RUN_FAILED",
            "NO_JOB_ROW",
            "PROBE_FAILED",
            # Slice 187 D6. Both are raised by the coverage-specific check
            # only; the generic evaluation below must never emit either.
            "CONTENT_EDGE_TOO_OLD",
            "CONTENT_EDGE_PROBE_FAILED",
        }

    def test_generic_evaluation_never_emits_the_coverage_content_signal(self) -> None:
        # The signal's contract is that it comes from check_coverage_freshness.
        # If _evaluate ever learns to raise it, the two layers' responsibilities
        # have blurred and the detection-floor documentation stops being true.
        verdict = _frozen_evaluate(  # type: ignore[arg-type]
            _EvalConnection(cagg_max=_NOW - timedelta(days=400)), _VIEW
        )
        assert StalenessSignal.CONTENT_EDGE_TOO_OLD not in verdict.signals

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
            rows=[
                (
                    1003,
                    True,
                    timedelta(days=1),
                    timedelta(hours=4),
                    "Success",
                    _utc(2026, 7, 26),
                )
            ]
        )
        job = _read_refresh_job(conn, _VIEW)  # type: ignore[arg-type]
        assert job is not None
        assert job.job_id == 1003
        assert job.scheduled is True
        assert job.start_offset == timedelta(days=1)
        assert job.end_offset == timedelta(hours=4)
        assert job.last_run_status == "Success"
        assert job.last_successful_finish == _utc(2026, 7, 26)

    def test_infinity_last_success_is_normalized_to_null_in_sql(self) -> None:
        # A policy created but never run stores '-infinity', which psycopg
        # cannot load into a datetime — it raises DataError mid-fetch and every
        # freshly-created cagg would refuse. Normalized in SQL instead.
        conn = _RecordingConnection(rows=[(1, True, None, None, "Success", None)])
        _read_refresh_job(conn, _VIEW)  # type: ignore[arg-type]
        catalog_sql = conn.log[-1][0]
        assert "nullif" in catalog_sql
        assert "-infinity" in catalog_sql

    def test_empty_result_returns_none(self) -> None:
        conn = _RecordingConnection(rows=[])
        assert _read_refresh_job(conn, _VIEW) is None  # type: ignore[arg-type]

    def test_view_name_is_a_bound_parameter_not_inlined(self) -> None:
        conn = _RecordingConnection(rows=[(1, True, None, None, "Success", None)])
        _read_refresh_job(conn, _VIEW)  # type: ignore[arg-type]
        catalog_sql, params = conn.log[-1]
        assert _VIEW not in catalog_sql, "view_name must not be interpolated"
        assert isinstance(params, tuple)
        assert params[0] == _VIEW

    def test_start_offset_is_cast_to_interval_in_sql(self) -> None:
        # TimescaleDB keeps start_offset in jobs.config as a jsonb interval
        # string; the cast is what makes psycopg return a timedelta.
        conn = _RecordingConnection(
            rows=[(1, True, timedelta(days=1), None, "Success", None)]
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

    def test_raw_probe_buckets_the_edge_when_given_a_width(self) -> None:
        # Both sides of the lag comparison must be bucket starts, otherwise a
        # healthy coarse cagg reports its own bucket width as lag (measured on
        # prod: daily_quarterly_ohlcv sat 72 days "behind" raw while healthy).
        conn = _RecordingConnection(rows=[(_utc(2026, 7, 24),)])
        _raw_max(conn, _RAW, "3 mons")  # type: ignore[arg-type]
        probe_sql, params = conn.log[-1]
        assert "time_bucket(" in probe_sql
        # The width is bound, not interpolated — variable-width month/quarter
        # buckets are aligned by PostgreSQL, never by Python arithmetic.
        assert "3 mons" not in probe_sql
        assert isinstance(params, tuple) and params[0] == "3 mons"

    def test_raw_probe_falls_back_to_plain_max_without_a_width(self) -> None:
        conn = _RecordingConnection(rows=[(_utc(2026, 7, 24),)])
        _raw_max(conn, _RAW)  # type: ignore[arg-type]
        assert "time_bucket(" not in conn.log[-1][0]

    def test_catalog_read_also_sets_the_timeout_first(self) -> None:
        conn = _RecordingConnection(rows=[(1, True, None, None, "Success", None)])
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

    def test_end_offset_is_added_to_the_budget(self) -> None:
        # A policy deliberately refuses to materialize the most recent
        # end_offset of data, so that much lag is configured, not stale.
        # daily_monthly_ohlcv's end_offset is 30 days (verified on prod).
        assert _resolve_threshold(
            timedelta(days=90), timedelta(days=30)
        ) == MAX_COVERAGE_SOURCE_STALENESS + timedelta(days=30)

    def test_ceiling_still_applies_with_an_end_offset(self) -> None:
        # end_offset widens the budget but must not defeat the ceiling on the
        # start_offset term itself.
        assert _resolve_threshold(timedelta(days=270), timedelta(hours=4)) == (
            MAX_COVERAGE_SOURCE_STALENESS + timedelta(hours=4)
        )


# --- Task 5: evaluation fixtures -------------------------------------------
#
# _evaluate issues exactly four queries in order: catalog read, bucket width,
# cagg edge, raw edge. The fake serves that sequence and can raise on any one.
# Both edges are bucket starts (the raw edge is bucketed in SQL), so a healthy
# fixture has them equal — the structural bucket-width offset is cancelled, not
# budgeted for.

_NOW = _utc(2026, 7, 26)
_CAGG_EDGE = _NOW - timedelta(hours=4)
_RAW_EDGE = _CAGG_EDGE
_BUCKET_WIDTH = "04:00:00"


class _Unset:
    """Sentinel distinguishing "argument omitted" from an explicit None."""


_UNSET = _Unset()


class _EvalConnection(_RecordingConnection):
    """Serves the catalog row, bucket width, then the two edge probes."""

    def __init__(
        self,
        *,
        scheduled: bool = True,
        start_offset: timedelta | None = timedelta(days=1),
        end_offset: timedelta | None = None,
        last_run_status: str | None = "Success",
        # Sentinel, not None: None is a meaningful value here (the cold-start
        # "policy created but never run" shape), so it must reach the catalog
        # row rather than being replaced by the default.
        last_successful_finish: datetime | None | _Unset = _UNSET,
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
                end_offset,
                last_run_status,
                _NOW
                if isinstance(last_successful_finish, _Unset)
                else (last_successful_finish),
            )
            if job_row
            else None
        )
        self._template: list[Any] = [
            catalog,
            (_BUCKET_WIDTH,),
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
        super().__init__(conn.log, conn._rows, conn)
        self._conn = conn

    def execute(self, query: object, params: object = None) -> None:
        super().execute(query, params)
        text = query if isinstance(query, str) else _render_sql(query)
        if "statement_timeout" in text:
            return
        self._conn._query_count += 1
        if self._conn._query_count == self._conn._raise_on_query:
            raise psycopg.OperationalError("canceling statement due to timeout")


def _frozen_evaluate(conn: Any, view_name: str) -> FreshnessVerdict:
    """Evaluate with the wall clock frozen at _NOW.

    Injects the clock explicitly rather than patching the module attribute:
    the dependency is visible at the call site and cannot be defeated by a
    future refactor of how the seam is resolved. ``TestClockSeam`` separately
    pins that monkeypatching ``_now`` also works, since the docstring on
    ``_now`` promises it.
    """
    return _evaluate(conn, view_name, now=lambda: _NOW)


class TestEvaluateSignals:
    """Task 5.3 — each D1 signal in isolation, the other three healthy."""

    def test_healthy_cagg_is_fresh(self) -> None:
        # Criterion 4: no false positive on a healthy cagg.
        verdict = _frozen_evaluate(_EvalConnection(), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is True
        assert verdict.signals == ()
        # Both edges are bucket starts, so a healthy cagg has zero lag — the
        # bucket width is cancelled by bucketing the raw edge, not budgeted for.
        assert verdict.lag == timedelta(0)

    def test_lag_exceeding_threshold_trips(self) -> None:
        # Raw ran four days past the cagg edge — the 163 incident's lag shape.
        verdict = _frozen_evaluate(  # type: ignore[arg-type]
            _EvalConnection(cagg_max=_NOW - timedelta(days=4)), _VIEW
        )
        assert verdict.is_fresh is False
        assert StalenessSignal.LAG_EXCEEDS_THRESHOLD in verdict.signals

    def test_paused_policy_trips(self) -> None:
        # The exact 163 incident: job 1003 left scheduled=false.
        verdict = _frozen_evaluate(_EvalConnection(scheduled=False), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert StalenessSignal.NOT_SCHEDULED in verdict.signals

    def test_stale_last_success_trips(self) -> None:
        verdict = _frozen_evaluate(  # type: ignore[arg-type]
            _EvalConnection(last_successful_finish=_NOW - timedelta(days=5)), _VIEW
        )
        assert verdict.is_fresh is False
        assert StalenessSignal.LAST_SUCCESS_TOO_OLD in verdict.signals

    def test_failing_last_run_trips(self) -> None:
        # Scheduled and firing, but failing every time — a scheduled-only check
        # reports this as healthy.
        verdict = _frozen_evaluate(_EvalConnection(last_run_status="Failed"), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert StalenessSignal.LAST_RUN_FAILED in verdict.signals

    def test_every_signal_that_fired_is_collected(self) -> None:
        verdict = _frozen_evaluate(  # type: ignore[arg-type]
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

    def test_never_run_policy_is_not_stale(self) -> None:
        # Cold start: a policy created moments ago on an already-materialized
        # cagg reports NULL last_successful_finish and NULL last_run_status
        # (verified against TimescaleDB 2.23 on 2026-07-26). That is a healthy
        # new cagg, not a stalled one — signalling on it would refuse reads on
        # every freshly-built cagg. Actual currency is still covered by the lag
        # signal, which does not depend on job history.
        verdict = _frozen_evaluate(  # type: ignore[arg-type]
            _EvalConnection(last_successful_finish=None, last_run_status=None),
            _VIEW,
        )
        assert verdict.is_fresh is True, verdict.detail
        assert StalenessSignal.LAST_SUCCESS_TOO_OLD not in verdict.signals

    def test_never_run_policy_still_trips_on_lag(self) -> None:
        # The cold-start exemption must not become a blind spot: a policy that
        # has never run AND whose cagg is behind is still stale.
        verdict = _frozen_evaluate(  # type: ignore[arg-type]
            _EvalConnection(
                last_successful_finish=None,
                last_run_status=None,
                cagg_max=_NOW - timedelta(days=4),
            ),
            _VIEW,
        )
        assert verdict.is_fresh is False
        assert StalenessSignal.LAG_EXCEEDS_THRESHOLD in verdict.signals

    def test_missing_job_row_trips(self) -> None:
        # A cagg with no refresh policy never self-heals.
        verdict = _frozen_evaluate(_EvalConnection(job_row=False), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert verdict.signals == (StalenessSignal.NO_JOB_ROW,)

    def test_non_cagg_view_name_raises_valueerror(self) -> None:
        # A caller bug must not be absorbed into a staleness refusal.
        with pytest.raises(ValueError, match="not a known continuous aggregate"):
            _frozen_evaluate(_EvalConnection(), "not_a_cagg")  # type: ignore[arg-type]

    @pytest.mark.parametrize("failing_query", [1, 2, 3, 4])
    def test_probe_error_trips_with_probe_failed(self, failing_query: int) -> None:
        # Whichever of the three queries raises, the verdict is a refusal and
        # the error never propagates into the reader's own error path.
        verdict = _frozen_evaluate(  # type: ignore[arg-type]
            _EvalConnection(raise_on_query=failing_query), _VIEW
        )
        assert verdict.is_fresh is False
        assert verdict.signals == (StalenessSignal.PROBE_FAILED,)
        assert verdict.lag is None


class TestStatementTimeoutRestoration:
    """Review F002 — the guard must put the caller's own timeout back.

    ``_set_probe_timeout`` uses a plain session-scoped ``SET`` (correct: under
    autocommit ``SET LOCAL`` is discarded and every probe would run unbounded).
    The restore therefore has to undo it, and restoring to ``DEFAULT`` silently
    discards a session-level ``statement_timeout`` the caller had set before
    calling the guard. No reader does that today, but the guard sits on a read
    path where one plausibly would.
    """

    def test_callers_timeout_is_restored_not_reset_to_default(self) -> None:
        conn = _EvalConnection()
        conn.statement_timeout = "60s"

        _frozen_evaluate(conn, _VIEW)  # type: ignore[arg-type]

        assert conn.statement_timeout == "60s"
        # And the probes really did run under the probe bound, not the caller's.
        assert any(
            f"SET statement_timeout = '{CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT}'" == sql
            for sql in conn.statements
        )

    def test_callers_timeout_is_restored_on_the_probe_failure_path(self) -> None:
        # The failure path has its own restore call; a probe timeout must not
        # leave the caller's session clamped to the probe's 10s bound.
        conn = _EvalConnection(raise_on_query=2)
        conn.statement_timeout = "60s"

        verdict = _frozen_evaluate(conn, _VIEW)  # type: ignore[arg-type]

        assert verdict.signals == (StalenessSignal.PROBE_FAILED,)
        assert conn.statement_timeout == "60s"

    def test_restore_falls_back_to_default_when_prior_is_unknown(self) -> None:
        # If the pre-probe read cannot answer, restoring to DEFAULT is the old
        # behavior and no worse than it.
        conn = _RecordingConnection(rows=[])
        _restore_probe_timeout(conn, None)  # type: ignore[arg-type]
        assert ("SET statement_timeout = DEFAULT", None) in conn.log


class TestClockSeam:
    """The ``now`` seam must be resolvable at call time, not bound at import.

    Regression guard. Both functions originally defaulted ``now`` to the
    module-level ``_now`` function object, which Python evaluates **once, at
    import time**. ``monkeypatch.setattr(cagg_freshness, "_now", ...)`` — the
    substitution ``_now``'s own docstring advertises — rebound the module
    attribute while the captured default kept pointing at the original. The
    freeze silently did nothing, so LAST_SUCCESS_TOO_OLD kept comparing
    against the real clock and began firing spuriously the moment the
    fixture's _NOW aged past the threshold: a suite that broke on a calendar
    boundary rather than on a code change.

    These assert the substitution works, so a future refactor that
    reintroduces an import-time default fails here instead of at midnight.
    """

    def test_monkeypatching_now_freezes_evaluate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # _NOW is the fixture's healthy "last success" instant. Patch the
        # module attribute only — no explicit now= — and a cagg that succeeded
        # at _NOW must read fresh no matter what the real clock says.
        monkeypatch.setattr(cagg_freshness, "_now", lambda: _NOW)
        verdict = _evaluate(_EvalConnection(), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is True
        assert StalenessSignal.LAST_SUCCESS_TOO_OLD not in verdict.signals

    def test_monkeypatching_now_is_observed_by_evaluate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The converse, so the test above cannot pass by the patch being
        # ignored: advance the patched clock far past the threshold and the
        # same healthy fixture must now trip LAST_SUCCESS_TOO_OLD.
        monkeypatch.setattr(cagg_freshness, "_now", lambda: _NOW + timedelta(days=30))
        verdict = _evaluate(_EvalConnection(), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert StalenessSignal.LAST_SUCCESS_TOO_OLD in verdict.signals

    def test_monkeypatching_now_is_observed_by_assert_cagg_fresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The public entry point takes the same seam and must resolve it the
        # same way; it is the one production readers call.
        reset_freshness_cache()
        monkeypatch.setattr(cagg_freshness, "_now", lambda: _NOW + timedelta(days=30))
        verdict = assert_cagg_fresh(_EvalConnection(), _VIEW)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert StalenessSignal.LAST_SUCCESS_TOO_OLD in verdict.signals


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

    def test_now_seam_reaches_the_staleness_evaluation_not_just_the_cache(
        self,
    ) -> None:
        # The seam must cover ALL time-dependent logic. The policy last
        # succeeded at _NOW; advancing the injected clock two days past it must
        # produce LAST_SUCCESS_TOO_OLD, which only happens if `now` reaches
        # _evaluate rather than governing cache expiry alone.
        conn = _EvalConnection(last_successful_finish=_NOW)
        much_later = _NOW + timedelta(days=2)
        verdict = assert_cagg_fresh(conn, _VIEW, now=lambda: much_later)  # type: ignore[arg-type]
        assert verdict.is_fresh is False
        assert StalenessSignal.LAST_SUCCESS_TOO_OLD in verdict.signals

    def test_distinct_view_names_do_not_share_an_entry(self) -> None:
        stale_conn = _EvalConnection(scheduled=False)
        fresh_conn = _EvalConnection()
        stale = assert_cagg_fresh(stale_conn, _VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        fresh = assert_cagg_fresh(fresh_conn, _DAILY_VIEW, now=lambda: _NOW)  # type: ignore[arg-type]
        assert stale.is_fresh is False
        assert fresh.is_fresh is True
        assert fresh.view_name == _DAILY_VIEW


_YEAR_BUCKET = "365 days"
"""``COVERAGE_BUCKET_INTERVAL`` as the catalog spells it — the width that makes
the generic guard's detection floor vacuous for the coverage caggs."""

_EPOCH = _utc(2000, 1, 1)
"""Bucket-grid origin. ``time_bucket`` on a fixed-width interval measures from
the PostgreSQL epoch; any fixed origin reproduces the aliasing this test is
about, and a stated one keeps the arithmetic below checkable by hand."""


def _align(moment: datetime, width: timedelta) -> datetime:
    """Floor ``moment`` onto a fixed-width bucket grid.

    Stands in for PostgreSQL's ``time_bucket``. Fixed-width only, which is all
    ``COVERAGE_BUCKET_INTERVAL`` needs — month/quarter widths are the reason
    the production code buckets in SQL rather than in Python.
    """
    elapsed = moment - _EPOCH
    return _EPOCH + (elapsed // width) * width


class _WideBucketConnection(_RecordingConnection):
    """A wide-bucket cagg whose raw edge is bucketed *by the fake*, the way the
    database would do it.

    This is the point of the fixture. ``_EvalConnection`` hands back an
    already-aligned ``raw_max``, so a test built on it would still report
    ``lag=0`` with the alignment step deleted and would prove nothing. Here the
    fake inspects the probe SQL: when ``_raw_max`` asks for
    ``time_bucket(...)`` it gets the floored edge, and when it asks for a plain
    ``max(time)`` it gets the true one. Removing the alignment therefore changes
    what this fixture returns, which is what makes the assertions below load
    bearing (slice 187 D6, task 3).
    """

    def __init__(self, *, raw_edge: datetime, cagg_edge: datetime) -> None:
        # Public: the cursor is handed these directly rather than reaching back
        # through the connection for private attributes (review F005).
        self.raw_edge = raw_edge
        self.cagg_edge = cagg_edge
        super().__init__(rows=[])

    def cursor(self) -> _RecordingCursor:
        return _BucketingCursor(self, raw_edge=self.raw_edge, cagg_edge=self.cagg_edge)


class _BucketingCursor(_RecordingCursor):
    """Answers each probe from the SQL it was handed rather than from a queue."""

    def __init__(
        self,
        conn: _WideBucketConnection,
        *,
        raw_edge: datetime,
        cagg_edge: datetime,
    ) -> None:
        super().__init__(conn.log, conn._rows, conn)
        self._raw_edge = raw_edge
        self._cagg_edge = cagg_edge
        self._answer: Any = None

    def execute(self, query: object, params: object = None) -> None:
        super().execute(query, params)
        text = (query if isinstance(query, str) else _render_sql(query)).lower()
        if "statement_timeout" in text:
            return
        if "timescaledb_information.jobs" in text:
            # Healthy policy: scheduled, succeeding, no offsets in play. Every
            # non-lag signal must stay silent so the assertions below are about
            # the lag measurement alone.
            self._answer = (1107, True, timedelta(days=750), None, "Success", _NOW)
        elif "bucket_function" in text or "continuous_agg" in text:
            self._answer = (_YEAR_BUCKET,)
        elif "max(" in text and "time_bucket(" in text:
            # _raw_max WITH alignment: the database floors the raw edge onto the
            # cagg's grid before returning it.
            self._answer = (_align(self._raw_edge, timedelta(days=365)),)
        elif '"minute_coverage"' in text or "time_bucket" in text:
            # _cagg_max: the cagg's own materialized edge, already a bucket start.
            self._answer = (self._cagg_edge,)
        else:
            # _raw_max WITHOUT alignment (plain max(time)) — the shape the probe
            # degrades to if the bucketing step is removed.
            self._answer = (self._raw_edge,)

    def fetchone(self) -> Any:
        if self._show_pending:
            return super().fetchone()
        return self._answer


class TestDetectionFloor:
    """Slice 187 D6 / task 3 — the generic guard cannot see inside one bucket.

    **A passing test here means the limitation is present and expected.** It
    does not mean the coverage caggs are fresh; on prod they are not. The
    content-edge check in ``status_coverage`` is what catches that, and it
    exists precisely because these assertions hold.

    The floor is a *boundary*, not a blanket refusal to report lag, so both
    sides of it are asserted: sub-bucket lag is invisible, supra-bucket lag is
    caught. A test asserting only the first would also pass against a guard
    that never reports anything.
    """

    _VIEW_NAME = "minute_coverage"
    _SOURCE = "minute_ohlcv"

    def _verdict(self, lag: timedelta) -> FreshnessVerdict:
        """Evaluate a wide-bucket cagg whose raw edge leads its own edge by
        ``lag``, with both edges inside the same year where ``lag`` is small."""
        cagg_edge = _align(_NOW, timedelta(days=365))
        conn = _WideBucketConnection(raw_edge=cagg_edge + lag, cagg_edge=cagg_edge)
        return _evaluate(
            conn,  # type: ignore[arg-type]
            self._VIEW_NAME,
            source_table=self._SOURCE,
            now=lambda: _NOW,
        )

    def test_lag_inside_one_bucket_is_invisible_to_the_generic_guard(self) -> None:
        # 52 days is the real prod figure for daily_coverage on 2026-08-04, and
        # it is nowhere near the 365-day bucket, so bucketing cancels it whole.
        verdict = self._verdict(timedelta(days=52))
        assert verdict.is_fresh is True, (
            "expected the documented detection floor: a 52-day lag inside a "
            "365-day bucket must report fresh. If this now fails, _raw_max's "
            "bucket alignment changed — see slice 187 D6 before 'fixing' it."
        )
        assert verdict.lag == timedelta(0)
        assert StalenessSignal.LAG_EXCEEDS_THRESHOLD not in verdict.signals

    def test_lag_exceeding_one_bucket_is_still_caught(self) -> None:
        # The floor is a boundary. Push the raw edge into the next bucket and
        # the same guard reports the staleness it just missed.
        verdict = self._verdict(timedelta(days=400))
        assert verdict.is_fresh is False
        assert StalenessSignal.LAG_EXCEEDS_THRESHOLD in verdict.signals
        assert verdict.lag is not None and verdict.lag >= timedelta(days=365)

    def test_verdict_exposes_the_bucket_width_that_sets_the_floor(self) -> None:
        # The floor is inspectable rather than implicit: a caller holding the
        # verdict can see the resolution limit of the lag it carries.
        assert self._verdict(timedelta(days=52)).bucket_width == _YEAR_BUCKET
