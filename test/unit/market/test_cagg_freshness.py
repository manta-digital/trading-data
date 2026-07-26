"""Unit tests for the slice 168 cagg freshness assertion.

Covers the verdict type and signal enum (task 2), the job-catalog read (task 3),
the edge probes, their timeout discipline, and threshold resolution (task 4),
the four-signal evaluation plus indeterminate handling (task 5), and the TTL
verdict cache (task 6). The DB is faked at the ``execute()`` boundary;
assertions are on call *order* and bound parameters, not SQL text.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Any, Literal

import pytest

from manta_trading.constants import (
    CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT,
    MAX_COVERAGE_SOURCE_STALENESS,
)
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
    _cagg_max,
    _raw_max,
    _read_refresh_job,
    _resolve_source_table,
    _resolve_threshold,
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
