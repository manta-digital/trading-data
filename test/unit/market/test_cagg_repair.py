"""Unit tests for the slice 163 cagg repair sweep and pre-flight.

Pre-flight (C2): refusal on unpaused refresh/columnstore jobs, wrong interval,
missing/insufficient headroom attestation; all-clear passes; the raw-table
guard. Sweep (C4): parity-skip, drop→refresh→compress ordering, the three D1
crash-window resume paths, and dry-run zero-mutation. The DB is mocked at the
execute() boundary and assertions are on call *order*, not SQL text.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from manta_trading.constants import MINUTE_CAGG_CHUNK_INTERVAL, Granularity
from manta_trading.market.maintenance.cagg_repair import (
    _REQUIRED_HEADROOM_GB,
    RepairError,
    _rebuild_window,
    _repair_one_cagg,
    preflight,
)
from manta_trading.market.maintenance.rechunk import PreflightError

_VIEW = "minute_4hour_ohlcv"
_MAT = "_materialized_hypertable_6"
# The cagg feeding the daemon coverage index. Equal to _VIEW today (the 4h
# cagg); named separately so the cross-granularity guard tests read clearly and
# survive the source constant changing.
_COVERAGE_VIEW = "minute_4hour_ohlcv"
# A repair target that is NOT the coverage-index cagg.
_OTHER_VIEW = "minute_1hour_ohlcv"


def _utc(y: int, m: int = 1, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# A SQL-dispatching mock connection
# ---------------------------------------------------------------------------


class _FakeCursorResult:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = many if many is not None else []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _FakeConn:
    """Mock psycopg connection whose execute() dispatches by SQL fragment.

    Records every executed SQL (in order) for call-order assertions. Parity
    counts, job rows, interval, and chunk rows are configurable per test.
    """

    def __init__(
        self,
        *,
        jobs=None,
        coverage_jobs=None,
        interval=MINUTE_CAGG_CHUNK_INTERVAL,
        parity_sequence=None,
        uncompressed_chunks=None,
    ):
        self.executed: list[str] = []
        self._jobs = jobs if jobs is not None else []
        # Jobs returned when the job query is scoped to the coverage-index cagg
        # (a different view than the repair target). None => reuse self._jobs,
        # which is what the same-view case wants.
        self._coverage_jobs = coverage_jobs
        self._interval = interval
        # parity_sequence: list of (raw, cagg) returned by successive parity
        # probes (one raw COUNT + one cagg SUM per window). We pop per window.
        self._parity = list(parity_sequence or [])
        self._parity_idx = 0
        self._uncompressed = (
            uncompressed_chunks if uncompressed_chunks is not None else []
        )
        # After a rebuild, subsequent parity for that window would pass; tests
        # that need that set parity_sequence accordingly.

    def execute(self, sql, params=None):
        self.executed.append(sql)
        s = sql
        if "FROM timescaledb_information.jobs" in s:
            # _resolve_cagg_jobs passes (view_name, [procs]) — dispatch on the
            # view so the coverage-index probe can differ from the target's.
            scoped_view = params[0] if params else None
            if self._coverage_jobs is not None and scoped_view == _COVERAGE_VIEW:
                return _FakeCursorResult(many=self._coverage_jobs)
            return _FakeCursorResult(many=self._jobs)
        if "materialization_hypertable_name" in s and "continuous_aggregates" in s:
            return _FakeCursorResult(one={"mat_name": _MAT})
        if "time_interval" in s and "dimensions" in s:
            return _FakeCursorResult(
                one={"time_interval": self._interval}
                if self._interval is not None
                else {"time_interval": None}
            )
        if s.strip().startswith("SELECT COUNT(*)"):
            raw, _cagg = self._parity[self._parity_idx]
            return _FakeCursorResult(one={"n": raw})
        if "SUM(minute_count)" in s:
            _raw, cagg = self._parity[self._parity_idx]
            self._parity_idx += 1  # advance after the cagg half of the pair
            return _FakeCursorResult(one={"n": cagg})
        if "drop_chunks" in s:
            return _FakeCursorResult(one={"drop_chunks": None})
        if "refresh_continuous_aggregate" in s:
            return _FakeCursorResult(one=None)
        if "FROM timescaledb_information.chunks" in s:
            return _FakeCursorResult(many=self._uncompressed)
        if "compress_chunk" in s:
            return _FakeCursorResult(one=None)
        if "clock_timestamp" in s:
            return _FakeCursorResult(one={"ts": _utc(2026, 7, 24)})
        if "SET statement_timeout" in s:
            return _FakeCursorResult(one=None)
        return _FakeCursorResult(one=None)


def _job(job_id: int, proc: str, scheduled: bool, view: str = _VIEW) -> dict:
    return {
        "job_id": job_id,
        "proc_name": proc,
        "hypertable_name": view,
        "scheduled": scheduled,
    }


_REFRESH = "policy_refresh_continuous_aggregate"
_COLUMNSTORE = "policy_compression"


# ---------------------------------------------------------------------------
# Pre-flight (Task C2)
# ---------------------------------------------------------------------------


class TestPreflight:
    def _run(self, conn, **kw):
        kw.setdefault("assume_headroom_gb", _REQUIRED_HEADROOM_GB)
        kw.setdefault("required_headroom_gb", _REQUIRED_HEADROOM_GB)
        preflight(conn, _VIEW, **kw)

    def test_unpaused_refresh_job_refuses(self):
        conn = _FakeConn(jobs=[_job(1003, _REFRESH, scheduled=True)])
        with pytest.raises(PreflightError, match="still scheduled"):
            self._run(conn)

    def test_unpaused_columnstore_job_refuses(self):
        conn = _FakeConn(jobs=[_job(2003, _COLUMNSTORE, scheduled=True)])
        with pytest.raises(PreflightError, match="still scheduled"):
            self._run(conn)

    def test_refusal_message_lists_job_ids_and_pause_command(self):
        conn = _FakeConn(jobs=[_job(1003, _REFRESH, scheduled=True)])
        with pytest.raises(PreflightError) as exc:
            self._run(conn)
        msg = str(exc.value)
        assert "1003" in msg
        assert "alter_job(1003, scheduled => false)" in msg

    def test_paused_coverage_index_cagg_refuses_for_other_target(self):
        """Repairing 1h while the 4h coverage cagg's refresh is paused refuses.

        Regression for the 2026-07-25 prod incident: a paused coverage cagg
        makes the minute daemon re-seed and re-pull recent sessions every cycle
        for the sweep's whole duration.
        """
        conn = _FakeConn(
            jobs=[_job(1002, _REFRESH, scheduled=False, view=_OTHER_VIEW)],
            coverage_jobs=[
                _job(1003, _REFRESH, scheduled=False, view=_COVERAGE_VIEW)
            ],
        )
        with pytest.raises(PreflightError, match="coverage index"):
            preflight(
                conn,
                _OTHER_VIEW,
                assume_headroom_gb=_REQUIRED_HEADROOM_GB,
                required_headroom_gb=_REQUIRED_HEADROOM_GB,
            )

    def test_coverage_refusal_names_resume_and_catchup(self):
        conn = _FakeConn(
            jobs=[_job(1002, _REFRESH, scheduled=False, view=_OTHER_VIEW)],
            coverage_jobs=[
                _job(1003, _REFRESH, scheduled=False, view=_COVERAGE_VIEW)
            ],
        )
        with pytest.raises(PreflightError) as exc:
            preflight(
                conn,
                _OTHER_VIEW,
                assume_headroom_gb=_REQUIRED_HEADROOM_GB,
                required_headroom_gb=_REQUIRED_HEADROOM_GB,
            )
        msg = str(exc.value)
        # Resuming alone is insufficient — the message must also point at the
        # catch-up refresh, which is the half operators miss.
        assert "alter_job(1003, scheduled => true)" in msg
        assert "refresh_continuous_aggregate" in msg
        assert "cagg-maintenance-pausing" in msg

    def test_scheduled_coverage_index_cagg_allows_other_target(self):
        conn = _FakeConn(
            jobs=[_job(1002, _REFRESH, scheduled=False, view=_OTHER_VIEW)],
            coverage_jobs=[
                _job(1003, _REFRESH, scheduled=True, view=_COVERAGE_VIEW)
            ],
        )
        preflight(
            conn,
            _OTHER_VIEW,
            assume_headroom_gb=_REQUIRED_HEADROOM_GB,
            required_headroom_gb=_REQUIRED_HEADROOM_GB,
        )  # must not raise

    def test_repairing_the_coverage_cagg_itself_is_allowed_while_paused(self):
        """The 4h sweep legitimately pauses its own refresh job — allow it."""
        conn = _FakeConn(
            jobs=[_job(1003, _REFRESH, scheduled=False, view=_COVERAGE_VIEW)]
        )
        self._run(conn)  # target IS the coverage cagg; must not raise

    def test_paused_coverage_columnstore_alone_does_not_refuse(self):
        """Only the *refresh* policy starves the index; columnstore is fine."""
        conn = _FakeConn(
            jobs=[_job(1002, _REFRESH, scheduled=False, view=_OTHER_VIEW)],
            coverage_jobs=[
                _job(1003, _REFRESH, scheduled=True, view=_COVERAGE_VIEW),
                _job(1021, _COLUMNSTORE, scheduled=False, view=_COVERAGE_VIEW),
            ],
        )
        preflight(
            conn,
            _OTHER_VIEW,
            assume_headroom_gb=_REQUIRED_HEADROOM_GB,
            required_headroom_gb=_REQUIRED_HEADROOM_GB,
        )  # must not raise

    def test_wrong_interval_refuses(self):
        conn = _FakeConn(
            jobs=[_job(1003, _REFRESH, scheduled=False)],
            interval=timedelta(days=1, hours=16),  # the wrong ~1.67d interval
        )
        with pytest.raises(PreflightError, match="chunk_time_interval"):
            self._run(conn)

    def test_missing_headroom_attestation_refuses(self):
        conn = _FakeConn(jobs=[_job(1003, _REFRESH, scheduled=False)])
        with pytest.raises(PreflightError, match="disk headroom"):
            self._run(conn, assume_headroom_gb=None)

    def test_insufficient_headroom_refuses(self):
        conn = _FakeConn(jobs=[_job(1003, _REFRESH, scheduled=False)])
        with pytest.raises(PreflightError, match="< required"):
            self._run(conn, assume_headroom_gb=1.0)

    def test_all_clear_passes(self):
        conn = _FakeConn(
            jobs=[
                _job(1003, _REFRESH, scheduled=False),
                _job(2003, _COLUMNSTORE, scheduled=False),
            ]
        )
        # Should not raise.
        self._run(conn)

    def test_raw_table_target_is_rejected_by_assertion(self):
        conn = _FakeConn(jobs=[])
        with pytest.raises(AssertionError, match="never target the raw"):
            preflight(
                conn,
                "minute_ohlcv",
                assume_headroom_gb=_REQUIRED_HEADROOM_GB,
                required_headroom_gb=_REQUIRED_HEADROOM_GB,
            )


# ---------------------------------------------------------------------------
# Window sweep (Task C4)
# ---------------------------------------------------------------------------


def _noop(_msg: str) -> None:
    pass


class TestSweep:
    def _windows(self, n: int):
        base = _utc(2019)
        return [
            (base + i * MINUTE_CAGG_CHUNK_INTERVAL,
             base + (i + 1) * MINUTE_CAGG_CHUNK_INTERVAL)
            for i in range(n)
        ]

    def test_parity_window_is_skipped_no_mutation(self):
        # One window, already at parity (raw == cagg) → no drop/refresh/compress.
        conn = _FakeConn(parity_sequence=[(1000, 1000)])
        outcome = _repair_one_cagg(
            conn, Granularity.H4, self._windows(1), dry_run=False, progress=_noop
        )
        assert outcome.already_done == 1
        assert outcome.rebuilt == 0
        assert not any("drop_chunks" in s for s in conn.executed)
        assert not any("refresh_continuous_aggregate" in s for s in conn.executed)

    def test_pending_window_rebuild_ordering(self):
        # One PENDING window → drop_chunks THEN refresh THEN compress, in order.
        conn = _FakeConn(
            parity_sequence=[(1000, 208)],
            uncompressed_chunks=[{"chunk": "_timescaledb_internal._hyper_6_1_chunk"}],
        )
        outcome = _repair_one_cagg(
            conn, Granularity.H4, self._windows(1), dry_run=False, progress=_noop
        )
        assert outcome.rebuilt == 1
        order = [
            s for s in conn.executed
            if any(k in s for k in ("drop_chunks", "refresh_continuous_aggregate",
                                    "compress_chunk"))
        ]
        drop_i = next(i for i, s in enumerate(order) if "drop_chunks" in s)
        refresh_i = next(
            i for i, s in enumerate(order) if "refresh_continuous_aggregate" in s
        )
        compress_i = next(i for i, s in enumerate(order) if "compress_chunk" in s)
        assert drop_i < refresh_i < compress_i

    def test_dry_run_performs_zero_mutations(self):
        conn = _FakeConn(parity_sequence=[(1000, 208)])
        outcome = _repair_one_cagg(
            conn, Granularity.H4, self._windows(1), dry_run=True, progress=_noop
        )
        assert outcome.planned_pending == 1
        assert outcome.rebuilt == 0
        for forbidden in ("drop_chunks", "refresh_continuous_aggregate",
                          "compress_chunk"):
            assert not any(forbidden in s for s in conn.executed)

    def test_resume_skips_already_parity_windows(self):
        # Two windows: first at parity (done on a prior run), second PENDING.
        # The resume run skips the first, rebuilds only the second.
        conn = _FakeConn(
            parity_sequence=[(1000, 1000), (1000, 208)],
            uncompressed_chunks=[{"chunk": "_timescaledb_internal._hyper_6_2_chunk"}],
        )
        outcome = _repair_one_cagg(
            conn, Granularity.H4, self._windows(2), dry_run=False, progress=_noop
        )
        assert outcome.already_done == 1
        assert outcome.rebuilt == 1
        # Exactly one drop_chunks (for the second window only).
        assert sum(1 for s in conn.executed if "drop_chunks" in s) == 1

    def test_kill_before_compress_skips_window_leaving_compression_to_policy(self):
        # Simulate the "kill after refresh, before compress" crash window: the
        # window is already at parity (refresh committed), so the resume run
        # sees DONE and does NOT drop/refresh again — and does NOT compress
        # either. Compression of such a chunk is the columnstore policy's job,
        # which is why the CLI's completion message insists the operator resume
        # the paused policies (review F008): until resumed, this chunk stays
        # uncompressed. Parity alone gates rebuild.
        conn = _FakeConn(parity_sequence=[(1000, 1000)])
        outcome = _repair_one_cagg(
            conn, Granularity.H4, self._windows(1), dry_run=False, progress=_noop
        )
        assert outcome.rebuilt == 0
        assert outcome.already_done == 1
        assert not any("drop_chunks" in s for s in conn.executed)


class TestRebuildWindow:
    def test_rebuild_issues_all_three_steps(self):
        conn = _FakeConn(
            uncompressed_chunks=[{"chunk": "_timescaledb_internal._hyper_6_1_chunk"}]
        )
        _rebuild_window(conn, _VIEW, _utc(2019), _utc(2019, 3, 12))
        assert any("drop_chunks" in s for s in conn.executed)
        assert any("refresh_continuous_aggregate" in s for s in conn.executed)
        assert any("compress_chunk" in s for s in conn.executed)

    def test_rebuild_compresses_multiple_edge_chunks(self):
        # A grid-straddling edge can yield two uncompressed chunks — both get
        # compressed.
        conn = _FakeConn(
            uncompressed_chunks=[
                {"chunk": "_timescaledb_internal._hyper_6_1_chunk"},
                {"chunk": "_timescaledb_internal._hyper_6_2_chunk"},
            ]
        )
        _rebuild_window(conn, _VIEW, _utc(2019), _utc(2019, 3, 12))
        assert sum(1 for s in conn.executed if "compress_chunk" in s) == 2


# ---------------------------------------------------------------------------
# Rebuild-failure wrapping (review F005)
# ---------------------------------------------------------------------------


class TestRebuildFailureWrapping:
    """A non-operational DB error during a window rebuild must surface as
    RepairError naming the failing window (the CLI maps it to exit code 2);
    OperationalError must propagate unwrapped so _TimeoutConnection's
    backend-cancel path and the CLI's database-error handler still see it."""

    def _one_window(self):
        base = _utc(2019)
        return [(base, base + MINUTE_CAGG_CHUNK_INTERVAL)]

    def test_db_error_during_rebuild_raises_repair_error_naming_window(self):
        class _FailingConn(_FakeConn):
            def execute(self, sql, params=None):
                if "drop_chunks" in sql:
                    raise psycopg.ProgrammingError("cannot drop chunk")
                return super().execute(sql, params)

        conn = _FailingConn(parity_sequence=[(1000, 208)])
        with pytest.raises(RepairError) as exc:
            _repair_one_cagg(
                conn, Granularity.H4, self._one_window(),
                dry_run=False, progress=_noop,
            )
        msg = str(exc.value)
        assert _VIEW in msg
        assert "2019-01-01" in msg  # the failing window is identified

    def test_operational_error_propagates_unwrapped(self):
        class _TimeoutConn(_FakeConn):
            def execute(self, sql, params=None):
                if "refresh_continuous_aggregate" in sql:
                    raise psycopg.OperationalError("statement timeout")
                return super().execute(sql, params)

        conn = _TimeoutConn(parity_sequence=[(1000, 208)])
        with pytest.raises(psycopg.OperationalError):
            _repair_one_cagg(
                conn, Granularity.H4, self._one_window(),
                dry_run=False, progress=_noop,
            )
