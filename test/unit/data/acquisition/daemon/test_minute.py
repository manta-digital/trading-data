"""Unit tests for run_minute_cycle and slice-148 extensions (T26, T7, T9)."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from manta_trading.constants import FetchEntryPoint
from manta_trading.data.acquisition.daemon.minute import (
    _do_minute_symbol,
    run_minute_cycle,
    run_minute_refetch,
)
from manta_trading.data.acquisition.quota import QuotaBucket, QuotaWaitAborted
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.gaps.actionable_gap_selector import GapRow

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _quota_bucket_in_context():
    """Slice 146 requires a QuotaBucket on the contextvar for any
    eodhd_get call."""
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    bucket = QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)
    token = QUOTA_BUCKET_VAR.set(bucket)
    yield bucket
    QUOTA_BUCKET_VAR.reset(token)


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, 0, tzinfo=UTC)


def _gap(start: datetime, end: datetime) -> GapRow:
    return GapRow(
        symbol="AAPL",
        granularity="minute",
        gap_start=start,
        gap_end=end,
        fetch_status="UNKNOWN",
        last_attempt_ts=None,
        attempt_count=1,
    )


def _make_snap() -> MagicMock:
    snap = MagicMock()
    snap.snapshot_id = "test-snap"
    snap.splits = ()
    snap.dividends = ()
    snap.prev_closes = {}
    return snap


class _FakeSettings:
    timescale_db_url = "postgresql://localhost/test"
    eodhd_api_key = "test-key"
    minute_history_start = None


class TestRunMinuteCycle:
    def _run(
        self,
        symbols: list[str],
        gaps_sequence: list,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
        gaps_inserted: int = 0,
        should_continue=None,
        eodhd_side_effect=None,
    ) -> tuple:
        gap_iter = iter(gaps_sequence)
        mocks: dict[str, MagicMock] = {}

        with ExitStack() as stack:

            def mp(target: str, **kwargs) -> MagicMock:
                m = stack.enter_context(patch(target, **kwargs))
                mocks[target] = m
                return m

            mp(
                "manta_trading.data.acquisition.daemon.minute.Settings",
                return_value=_FakeSettings(),
            )
            if eodhd_side_effect is not None:
                mp(
                    "manta_trading.data.acquisition.daemon.minute.eodhd_get",
                    side_effect=eodhd_side_effect,
                )
            mp(
                "manta_trading.data.acquisition.daemon.minute.classify_outcome",
                return_value=outcome,
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.outcome_to_fetch_status",
                return_value=None,
            )

            pick_mock = mp(
                "manta_trading.data.acquisition.daemon.minute.pick_most_recent_actionable_gap",
                side_effect=lambda *a, **kw: next(gap_iter, None),
            )
            update_mock = mp(
                "manta_trading.data.acquisition.daemon.minute.update_data_gaps",
                return_value=MagicMock(gaps_inserted=gaps_inserted),
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.build_minute_coverage_index",
                return_value={},
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.compute_missing_minute_sessions",
                return_value=[],
            )
            from manta_trading.constants import EODHD_INTRADAY_HORIZON
            from datetime import datetime as _datetime, timezone as _tz

            mp(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                return_value=_datetime(
                    EODHD_INTRADAY_HORIZON.year,
                    EODHD_INTRADAY_HORIZON.month,
                    EODHD_INTRADAY_HORIZON.day,
                    tzinfo=_tz.utc,
                ),
            )
            advance_mock = mp(
                "manta_trading.data.acquisition.daemon.minute._advance_minute_gap",
                return_value=None,
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute._record_minute_attempt",
                return_value=None,
            )
            coalesce_mock = mp(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
            )
            mp("manta_trading.data.acquisition.daemon.minute._insert_minute_bars")

            # advisory_lock as a passthrough context manager
            lock_cm = MagicMock()
            lock_cm.__enter__ = MagicMock(return_value=None)
            lock_cm.__exit__ = MagicMock(return_value=False)
            mp(
                "manta_trading.data.acquisition.daemon.minute.advisory_lock",
                return_value=lock_cm,
            )

            mock_pool_cls = mp(
                "manta_trading.data.acquisition.daemon.minute.ConnectionPool"
            )
            mock_http_cls = mp(
                "manta_trading.data.acquisition.daemon.minute.httpx.Client"
            )

            # Pool setup
            mock_pool = MagicMock()
            mock_pool_cls.return_value.__enter__ = MagicMock(return_value=mock_pool)
            mock_pool_cls.return_value.__exit__ = MagicMock(return_value=False)

            conn = MagicMock()
            txn = MagicMock()
            txn.__enter__ = MagicMock(return_value=txn)
            txn.__exit__ = MagicMock(return_value=False)
            conn.transaction.return_value = txn
            mock_pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
            mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

            # HTTP setup
            mock_http = MagicMock()
            mock_http_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_http_cls.return_value.__exit__ = MagicMock(return_value=False)
            # Return at least one bar so the `if bars:` branch executes
            mock_http.get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value=[
                        {
                            "timestamp": 1704196200,
                            "open": "100",
                            "high": "101",
                            "low": "99",
                            "close": "100",
                            "volume": "1000",
                        }
                    ]
                ),
            )

            report = run_minute_cycle(
                symbols=symbols, should_continue=should_continue
            )

        return report, pick_mock, update_mock, coalesce_mock, advance_mock

    def test_three_chunk_fetches_for_multi_month_gap(self) -> None:
        g1 = _gap(_dt(2024, 1, 1), _dt(2024, 4, 30))
        g2 = _gap(_dt(2024, 5, 1), _dt(2024, 8, 31))
        g3 = _gap(_dt(2024, 9, 1), _dt(2024, 12, 31))
        gaps = [g1, g2, g3, None]

        _, pick_mock, update_mock, coalesce_mock, advance_mock = self._run(
            ["AAPL"], gaps
        )

        # 1 initial seed via update_data_gaps
        assert update_mock.call_count == 1
        # 3 chunks → 3 _advance_minute_gap calls
        assert advance_mock.call_count == 3
        assert coalesce_mock.call_count == 1
        # 3 gaps returned + 1 None to terminate
        assert pick_mock.call_count == 4

    def test_coalesce_called_once_after_loop(self) -> None:
        gaps = [_gap(_dt(2024, 1, 1), _dt(2024, 3, 31)), None]
        _, _, _, coalesce_mock, _ = self._run(["AAPL"], gaps)
        assert coalesce_mock.call_count == 1

    def test_success_count(self) -> None:
        report, _, _, _, _ = self._run(
            ["AAPL"], [None], outcome=LastAttemptOutcome.SUCCESS
        )
        assert report.success_count == 1

    def test_multiple_symbols_each_get_own_gap_loop(self) -> None:
        # Two symbols; each gets None immediately
        gaps = [None, None]
        report, _, update_mock, coalesce_mock, _ = self._run(["AAPL", "MSFT"], gaps)
        # Each symbol does 1 initial update_data_gaps call
        assert update_mock.call_count == 2
        # Each symbol does 1 coalesce
        assert coalesce_mock.call_count == 2

    def test_seed_progress_accumulates_gaps_seeded_across_symbols(self, caplog) -> None:
        """slice 162: seed-phase progress sums gaps_inserted across all symbols
        and emits a completion INFO line with the accumulated total."""
        import logging

        gaps = [None, None, None]
        with caplog.at_level(
            logging.INFO, logger="manta_trading.data.acquisition.daemon.minute"
        ):
            self._run(["AAPL", "MSFT", "GOOG"], gaps, gaps_inserted=3)

        complete_lines = [
            r.message for r in caplog.records if "minute seed: complete" in r.message
        ]
        assert len(complete_lines) == 1
        assert "3 symbols" in complete_lines[0]
        assert "9 gap rows seeded" in complete_lines[0]

    def test_should_continue_false_mid_symbol_exits_between_chunks(self) -> None:
        """Shutdown mid-symbol stops after the current chunk, not after the
        whole symbol (20260807 clean-exit fix).

        Flag polls: 1 = between symbols, 2 = first chunk top (both pass),
        3 = second chunk top (stop). Exactly one chunk is fetched and the
        post-loop bookkeeping (coalesce) still runs.
        """
        g1 = _gap(_dt(2024, 1, 1), _dt(2024, 4, 30))
        g2 = _gap(_dt(2024, 5, 1), _dt(2024, 8, 31))
        g3 = _gap(_dt(2024, 9, 1), _dt(2024, 12, 31))
        polls = {"n": 0}

        def flag() -> bool:
            polls["n"] += 1
            return polls["n"] <= 2

        report, pick_mock, _, coalesce_mock, advance_mock = self._run(
            ["AAPL"], [g1, g2, g3, None], should_continue=flag
        )
        assert pick_mock.call_count == 1
        assert advance_mock.call_count == 1
        coalesce_mock.assert_called_once()

    def test_quota_wait_aborted_exits_cycle_without_transient_failure(self) -> None:
        """QuotaWaitAborted mid-fetch is shutdown, not a symbol failure: the
        cycle exits before the next symbol and records no outcome for it."""
        g1 = _gap(_dt(2024, 1, 1), _dt(2024, 4, 30))
        report, pick_mock, *_ = self._run(
            ["AAPL", "MSFT"],
            [g1, None, g1, None],
            eodhd_side_effect=QuotaWaitAborted("shutdown"),
        )
        assert pick_mock.call_count == 1  # MSFT never started
        assert report.transient_failure_count == 0
        assert report.symbol_outcomes == {}


# ---------------------------------------------------------------------------
# T7: _do_minute_symbol extensions (force_reset_terminal + window)
# ---------------------------------------------------------------------------


class _FakeSettings:
    timescale_db_url = "postgresql://localhost/test"
    eodhd_api_key = "test-key"
    minute_history_start = None


class TestDoMinuteSymbolExtensions:
    """Tests for the force_reset_terminal and window params added in slice 148."""

    def _run_do_minute(
        self,
        force_reset_terminal: bool = False,
        window: tuple[date, date] | None = None,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
        gaps_sequence: list | None = None,
        coverage_index: dict | None = None,
        precomputed_ranges: list | None = None,
        update_gaps_result: MagicMock | None = None,
    ) -> tuple[tuple, MagicMock, MagicMock, MagicMock]:
        """Call _do_minute_symbol with all external deps mocked.

        Returns (result, update_gaps_mock, coalesce_mock, compute_missing_mock).
        """
        if gaps_sequence is None:
            gaps_sequence = [None]  # no gaps → loop exits immediately

        gap_iter = iter(gaps_sequence)
        mock_update_gaps = MagicMock(
            return_value=update_gaps_result or MagicMock(gaps_inserted=0)
        )
        mock_coalesce = MagicMock(return_value=0)
        mock_compute_missing = MagicMock(return_value=precomputed_ranges or [])
        conn = MagicMock()
        txn = MagicMock()
        txn.__enter__ = MagicMock(return_value=txn)
        txn.__exit__ = MagicMock(return_value=False)
        conn.transaction.return_value = txn
        lock_cm = MagicMock()
        lock_cm.__enter__ = MagicMock(return_value=None)
        lock_cm.__exit__ = MagicMock(return_value=False)

        pool = MagicMock()
        pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
        pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value=[
                    {
                        "timestamp": 1704196200,
                        "open": "100",
                        "high": "101",
                        "low": "99",
                        "close": "100",
                        "volume": "1000",
                    }
                ]
            ),
        )

        from manta_trading.constants import EODHD_INTRADAY_HORIZON
        from datetime import datetime as _datetime, timezone as _tz

        resolved_start = _datetime(
            EODHD_INTRADAY_HORIZON.year,
            EODHD_INTRADAY_HORIZON.month,
            EODHD_INTRADAY_HORIZON.day,
            tzinfo=_tz.utc,
        )

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.classify_outcome",
                return_value=outcome,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.outcome_to_fetch_status",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.update_data_gaps",
                mock_update_gaps,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.compute_missing_minute_sessions",
                mock_compute_missing,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._advance_minute_gap",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._record_minute_attempt",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                return_value=resolved_start,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                mock_coalesce,
            ),
            patch("manta_trading.data.acquisition.daemon.minute._insert_minute_bars"),
            patch(
                "manta_trading.data.acquisition.daemon.minute.advisory_lock",
                return_value=lock_cm,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.eodhd_get",
                return_value=http.get.return_value,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.pick_most_recent_actionable_gap",
                side_effect=lambda *a, **kw: next(gap_iter, None),
            ),
        ):
            result = _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=http,
                settings=_FakeSettings(),
                via=FetchEntryPoint.CYCLE,
                force_reset_terminal=force_reset_terminal,
                window=window,
                coverage_index=coverage_index,
            )
        return result, mock_update_gaps, mock_coalesce, mock_compute_missing

    def test_force_reset_terminal_true_forwarded_to_initial_update_data_gaps(
        self,
    ) -> None:
        _, mock_update, _, _ = self._run_do_minute(force_reset_terminal=True)
        first_call_kwargs = mock_update.call_args_list[0].kwargs
        assert first_call_kwargs["force_reset_terminal"] is True

    def test_force_reset_terminal_false_default_forwarded(self) -> None:
        _, mock_update, _, _ = self._run_do_minute(force_reset_terminal=False)
        first_call_kwargs = mock_update.call_args_list[0].kwargs
        assert first_call_kwargs["force_reset_terminal"] is False

    def test_window_none_uses_resolved_history_start(self) -> None:
        """window=None → history_start = _resolve_minute_history_start()."""
        _, mock_update, _, _ = self._run_do_minute(window=None)
        from_ts = mock_update.call_args_list[0].args[3]
        # _run_do_minute patches the resolver to return a known datetime —
        # asserting the patched value flows into update_data_gaps args[3].
        from manta_trading.data.acquisition.daemon.minute import (
            EODHD_INTRADAY_HORIZON,
        )

        # Resolver default (mocked) is the EODHD intraday horizon.
        assert from_ts.date() == EODHD_INTRADAY_HORIZON

    def test_window_constrains_history_start(self) -> None:
        """window=(date1, date2) → history_start = max(window_start, resolved floor)."""
        future_start = date(2025, 1, 1)
        w = (future_start, date(2025, 12, 31))
        _, mock_update, _, _ = self._run_do_minute(window=w)
        from_ts = mock_update.call_args_list[0].args[3]
        # window_start (2025-01-01) is well above the EODHD horizon (2004-01-01),
        # so history_start should equal window_start.
        assert from_ts.date() == future_start

    def test_coalesce_called_after_chunk_loop(self) -> None:
        """coalesce_data_gaps must be called after the chunk loop."""
        _, _, mock_coalesce, _ = self._run_do_minute()
        mock_coalesce.assert_called_once()
        args = mock_coalesce.call_args.args
        assert args[1] == "AAPL"
        assert args[2] == "minute"

    def test_coverage_index_present_passes_precomputed_ranges_not_span(self) -> None:
        """slice 162: with a coverage index, seed uses coverage-derived ranges."""
        from manta_trading.data.gaps.compute_missing_ranges import GapRange

        ranges = [GapRange("AAPL", "minute", _dt(2024, 6, 10), _dt(2024, 6, 12))]
        _, mock_update, _, mock_compute_missing = self._run_do_minute(
            coverage_index={"AAPL": {date(2024, 1, 1)}},
            precomputed_ranges=ranges,
        )
        mock_compute_missing.assert_called_once()
        first_call_kwargs = mock_update.call_args_list[0].kwargs
        assert first_call_kwargs["precomputed_ranges"] == ranges
        # Not the legacy single [history_start, target_end] span behavior —
        # precomputed_ranges must be the coverage-derived list, not None.
        assert first_call_kwargs["precomputed_ranges"] is not None

    def test_coverage_index_none_skips_coverage_seeding_no_full_window_fallback(
        self,
    ) -> None:
        """slice 162 fail-safe: coverage_index=None must not compute_missing_minute_sessions,
        and update_data_gaps must receive precomputed_ranges=None (its own legacy
        single-span fallback), never a coverage-aware call that never happened."""
        _, mock_update, _, mock_compute_missing = self._run_do_minute(
            coverage_index=None
        )
        mock_compute_missing.assert_not_called()
        first_call_kwargs = mock_update.call_args_list[0].kwargs
        assert first_call_kwargs["precomputed_ranges"] is None

    def test_gaps_seeded_returned_from_update_result(self) -> None:
        """The 5th return element reflects update_data_gaps' gaps_inserted count."""
        result, _, _, _ = self._run_do_minute(
            coverage_index={"AAPL": set()},
            update_gaps_result=MagicMock(gaps_inserted=7),
        )
        gaps_seeded = result[4]
        assert gaps_seeded == 7

    def test_coalesce_called_on_refetch_path_too(self) -> None:
        """coalesce called even when force_reset_terminal=True."""
        _, _, mock_coalesce, _ = self._run_do_minute(force_reset_terminal=True)
        mock_coalesce.assert_called_once()

    def test_happy_path_logs_via_marker(self) -> None:
        """slice 165: a SUCCESSFUL fetch must emit at least one log line
        carrying via= — error-path-only markers leave the happy path
        unidentifiable, the exact ambiguity the slice exists to close."""
        with patch(
            "manta_trading.data.acquisition.daemon.minute._logger"
        ) as mock_logger:
            self._run_do_minute()
        via_calls = [
            call for call in mock_logger.info.call_args_list
            if "cycle" in call.args
        ]
        assert via_calls, "no INFO line carried via='cycle' on the happy path"


class TestViaMarkerThreading:
    """Tests for the via=refetch|cycle log marker added in slice 165."""

    def test_process_minute_symbol_forwards_via_to_do_minute_symbol(self) -> None:
        from manta_trading.data.acquisition.daemon.minute import (
            _process_minute_symbol,
        )

        mock_do = MagicMock(
            return_value=(LastAttemptOutcome.SUCCESS, None, None, 0, 0)
        )
        with patch(
            "manta_trading.data.acquisition.daemon.minute._do_minute_symbol",
            mock_do,
        ):
            _process_minute_symbol(
                "AAPL",
                pool=MagicMock(),
                http=MagicMock(),
                settings=_FakeSettings(),
                via=FetchEntryPoint.CYCLE,
            )
        assert mock_do.call_args.kwargs["via"] == "cycle"

    def test_process_minute_symbol_error_path_logs_via(self) -> None:
        from manta_trading.data.acquisition.daemon.minute import (
            _process_minute_symbol,
        )

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute._do_minute_symbol",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._logger"
            ) as mock_logger,
        ):
            outcome, *_ = _process_minute_symbol(
                "AAPL",
                pool=MagicMock(),
                http=MagicMock(),
                settings=_FakeSettings(),
                via=FetchEntryPoint.REFETCH,
            )
        assert outcome == LastAttemptOutcome.TRANSIENT_FAILURE
        logged_args = mock_logger.exception.call_args.args
        assert "refetch" in logged_args


# ---------------------------------------------------------------------------
# T9: run_minute_refetch tests
# ---------------------------------------------------------------------------


class TestRunMinuteRefetch:
    """Tests for the run_minute_refetch entry point added in slice 148."""

    def _run_refetch(
        self,
        symbol: str = "AAPL",
        from_date: date | None = None,
        to_date: date | None = None,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
        coverage_index: dict | None = None,
    ) -> tuple:
        mock_do_minute = MagicMock(return_value=(outcome, None, None, 0, 0))
        # The resolver returns 2010-01-01 (later than the EODHD horizon) so
        # tests can assert the per-symbol floor flows through.
        mock_resolve = MagicMock(return_value=datetime(2010, 1, 1, tzinfo=timezone.utc))
        mock_last_session = MagicMock(return_value=_dt(2024, 12, 31))
        mock_build_coverage = MagicMock(
            return_value=coverage_index
            if coverage_index is not None
            else {symbol: set()}
        )

        conn = MagicMock()
        txn = MagicMock()
        txn.__enter__ = MagicMock(return_value=txn)
        txn.__exit__ = MagicMock(return_value=False)
        conn.transaction.return_value = txn
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.Settings",
                return_value=_FakeSettings(),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._do_minute_symbol",
                mock_do_minute,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                mock_resolve,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._last_completed_session",
                mock_last_session,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.build_symbol_minute_coverage",
                mock_build_coverage,
            ),
            patch("manta_trading.data.acquisition.daemon.minute.httpx.Client"),
            patch(
                "manta_trading.data.acquisition.daemon.minute.ConnectionPool"
            ) as mock_pool_cls,
        ):
            mock_pool_cls.return_value.__enter__ = MagicMock(return_value=mock_pool)
            mock_pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            report = run_minute_refetch(symbol, from_date=from_date, to_date=to_date)

        return report, mock_do_minute, mock_build_coverage

    def test_from_date_none_uses_resolved_floor(self) -> None:
        """from_date=None → resolved_from = _resolve_minute_history_start()."""
        _, mock_do, _ = self._run_refetch(from_date=None)
        call_kwargs = mock_do.call_args.kwargs
        window_from = call_kwargs["window"][0]
        # Resolver mock returns 2010-01-01.
        assert window_from == date(2010, 1, 1)

    def test_to_date_none_resolves_to_last_completed_session(self) -> None:
        _, mock_do, _ = self._run_refetch(to_date=None)
        call_kwargs = mock_do.call_args.kwargs
        window_to = call_kwargs["window"][1]
        assert window_to == date(2024, 12, 31)

    def test_explicit_window_passed_through(self) -> None:
        fd = date(2024, 6, 1)
        td = date(2024, 9, 30)
        _, mock_do, _ = self._run_refetch(from_date=fd, to_date=td)
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["window"] == (fd, td)

    def test_force_reset_terminal_always_true(self) -> None:
        _, mock_do, _ = self._run_refetch()
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["force_reset_terminal"] is True

    def test_success_outcome_increments_success_count(self) -> None:
        report, _, _ = self._run_refetch(outcome=LastAttemptOutcome.SUCCESS)
        assert report.success_count == 1
        assert report.total == 1

    def test_via_refetch_passed_to_do_minute_symbol(self) -> None:
        _, mock_do, _ = self._run_refetch()
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["via"] == "refetch"

    def test_builds_symbol_coverage_and_forwards_to_do_minute_symbol(self) -> None:
        """slice 165: run_minute_refetch must build a per-symbol coverage
        index and pass it through — same seeding algorithm as the daemon,
        scoped to the one requested symbol (design §Amendment 2026-07-28)."""
        index = {"AAPL": {date(2024, 1, 1)}}
        _, mock_do, mock_build_coverage = self._run_refetch(coverage_index=index)
        mock_build_coverage.assert_called_once()
        # Called with (conn, symbol) — the requested symbol, positionally.
        assert mock_build_coverage.call_args.args[1] == "AAPL"
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["coverage_index"] == index

    def test_coverage_built_exactly_once_per_invocation(self) -> None:
        _, _, mock_build_coverage = self._run_refetch()
        assert mock_build_coverage.call_count == 1


# ---------------------------------------------------------------------------
# T11: _has_any_gaps re-fire regression (slice 162)
# ---------------------------------------------------------------------------


class TestHasAnyGapsRefireRegression:
    """Pins: a symbol WITH bars whose gap rows were deleted (so _has_any_gaps
    is false and _needs_seed fires) must re-seed only genuinely-missing
    sessions — never a full [history_start, today] span.

    Exercises the real compute_missing_minute_sessions (not mocked) against a
    controlled coverage index and session calendar, so the diff logic itself
    is under test, not just the wiring.
    """

    def test_refire_seeds_only_real_holes_not_full_history_span(self) -> None:
        history_start = datetime(2004, 1, 1, tzinfo=UTC)
        target_end = datetime(2024, 12, 31, tzinfo=UTC)

        # Symbol has bars for every session except one interior hole
        # (2024-06-11). _has_any_gaps is false (gap rows were deleted), so
        # _needs_seed fires purely on that trigger — coverage_index is what
        # must recreate only the real hole.
        sessions = [_dt(2024, 6, 10), _dt(2024, 6, 11), _dt(2024, 6, 12)]
        coverage_index = {"AAPL": {_dt(2024, 6, 10).date(), _dt(2024, 6, 12).date()}}

        mock_update_gaps = MagicMock(return_value=MagicMock(gaps_inserted=0))
        mock_coalesce = MagicMock(return_value=0)
        conn = MagicMock()
        txn = MagicMock()
        txn.__enter__ = MagicMock(return_value=txn)
        txn.__exit__ = MagicMock(return_value=False)
        conn.transaction.return_value = txn
        lock_cm = MagicMock()
        lock_cm.__enter__ = MagicMock(return_value=None)
        lock_cm.__exit__ = MagicMock(return_value=False)

        pool = MagicMock()
        pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
        pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        http = MagicMock()
        gap_iter = iter([None])  # no chunk gaps → loop exits immediately

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.update_data_gaps",
                mock_update_gaps,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._advance_minute_gap",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._record_minute_attempt",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                return_value=history_start,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                mock_coalesce,
            ),
            patch("manta_trading.data.acquisition.daemon.minute._insert_minute_bars"),
            patch(
                "manta_trading.data.acquisition.daemon.minute.advisory_lock",
                return_value=lock_cm,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.pick_most_recent_actionable_gap",
                side_effect=lambda *a, **kw: next(gap_iter, None),
            ),
            # Real compute_missing_minute_sessions runs; only clamp_to_lifecycle
            # and fetch_sessions (its DB I/O boundary) are patched.
            patch(
                "manta_trading.data.gaps.minute_coverage.clamp_to_lifecycle",
                return_value=(history_start, target_end),
            ),
            patch(
                "manta_trading.data.gaps.minute_coverage.fetch_sessions",
                return_value=sessions,
            ),
        ):
            result = _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=http,
                settings=_FakeSettings(),
                via=FetchEntryPoint.CYCLE,
                window=(date(2024, 6, 10), date(2024, 6, 12)),
                coverage_index=coverage_index,
            )

        assert result[4] == 0  # gaps_seeded reported via update_data_gaps mock (0 here)
        first_call_kwargs = mock_update_gaps.call_args_list[0].kwargs
        seeded_ranges = first_call_kwargs["precomputed_ranges"]
        assert seeded_ranges is not None
        assert len(seeded_ranges) == 1
        assert seeded_ranges[0].gap_start_utc == _dt(2024, 6, 11)
        assert seeded_ranges[0].gap_end_utc == _dt(2024, 6, 11)
        # Never the legacy full-history span
        assert not any(
            r.gap_start_utc == history_start and r.gap_end_utc == target_end
            for r in seeded_ranges
        )


class TestBarToRow:
    """Per-bar conversion guard: a malformed provider bar is skipped with a
    warning, never inserted with a fabricated price (CVR/LFWD incident,
    2026-08-14: EODHD sent open=null, Decimal raised InvalidOperation, and
    the whole symbol batch aborted as a spurious transient failure)."""

    GOOD = {
        "timestamp": 1755093000,
        "open": "10.5",
        "high": "10.9",
        "low": "10.1",
        "close": "10.7",
        "volume": 1200,
    }

    def _row(self, **overrides):
        from manta_trading.data.acquisition.daemon.minute import _bar_to_row

        return _bar_to_row("CVR", {**self.GOOD, **overrides})

    def test_good_bar_converts(self) -> None:
        row = self._row()
        assert row is not None
        ts, symbol, o, h, lo, c, v = row
        assert symbol == "CVR"
        assert (str(o), str(h), str(lo), str(c), v) == (
            "10.5",
            "10.9",
            "10.1",
            "10.7",
            1200,
        )
        assert ts.tzinfo is not None

    def test_null_open_skipped_not_raised(self, caplog) -> None:
        """The CVR/LFWD payload shape: open=None -> Decimal('None') raises
        InvalidOperation, which must be swallowed by the skip path."""
        assert self._row(open=None) is None
        assert "Skipping malformed minute bar" in caplog.text

    def test_missing_price_field_skipped_no_silent_default(self) -> None:
        bad = {k: v for k, v in self.GOOD.items() if k != "close"}
        from manta_trading.data.acquisition.daemon.minute import _bar_to_row

        assert _bar_to_row("CVR", bad) is None

    def test_nan_price_skipped(self) -> None:
        """Decimal('NaN') parses successfully — the finite check must
        reject it."""
        assert self._row(high="NaN") is None

    def test_zero_price_skipped(self) -> None:
        assert self._row(low=0) is None

    def test_negative_price_skipped(self) -> None:
        assert self._row(close="-1.25") is None

    def test_null_volume_defaults_to_zero(self) -> None:
        row = self._row(volume=None)
        assert row is not None
        assert row[6] == 0

    def test_insert_filters_bad_bars_keeps_good(self) -> None:
        """One bad bar must not abort the batch: good rows still reach COPY."""
        from manta_trading.data.acquisition.daemon.minute import (
            _insert_minute_bars,
        )

        conn = MagicMock()
        copy_rows: list[tuple] = []
        copy_cm = conn.cursor.return_value.__enter__.return_value.copy
        copy_cm.return_value.__enter__.return_value.write_row.side_effect = (
            copy_rows.append
        )
        _insert_minute_bars(conn, "CVR", [self.GOOD, {**self.GOOD, "open": None}])
        assert len(copy_rows) == 1
