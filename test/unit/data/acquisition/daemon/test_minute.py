"""Unit tests for run_minute_cycle and slice-148 extensions (T26, T7, T9)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import httpx
import psycopg
import pytest
from psycopg_pool import PoolTimeout

from manta_trading.constants import FetchEntryPoint
from manta_trading.data.acquisition.daemon.minute import (
    MINUTE_EXIT_OK,
    MINUTE_EXIT_PASS_INCOMPLETE,
    MinuteSymbolResult,
    _do_minute_symbol,
    minute_pass_exit_code,
    run_minute_cycle,
    run_minute_refetch,
)
from manta_trading.data.acquisition.quota import QuotaBucket, QuotaWaitAborted
from manta_trading.data.acquisition.daemon.daily import CycleReport
from manta_trading.data.acquisition.state import (
    LastAttemptOutcome,
    MinuteFailureKind,
    MinutePassOutcome,
)
from manta_trading.constants import (
    MAX_RETRY_COUNT,
    MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES,
    MINUTE_TRAILING_MAX_CHUNKS_PER_SYMBOL,
    MINUTE_TRAILING_PRIORITY_WINDOW,
    MinutePassPhase,
)
from manta_trading.data.gaps.actionable_gap_selector import GapRow
from manta_trading.data.quality.fetch_status import FetchStatus

UTC = timezone.utc

if TYPE_CHECKING:
    from manta_trading.config import Settings


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

            report = run_minute_cycle(symbols=symbols, should_continue=should_continue)

        return report, pick_mock, update_mock, coalesce_mock, advance_mock

    def test_three_chunk_fetches_for_multi_month_gap(self) -> None:
        g1 = _gap(_dt(2024, 1, 1), _dt(2024, 4, 30))
        g2 = _gap(_dt(2024, 5, 1), _dt(2024, 8, 31))
        g3 = _gap(_dt(2024, 9, 1), _dt(2024, 12, 31))
        gaps = [g1, g2, g3, None]

        _, pick_mock, update_mock, coalesce_mock, advance_mock = self._run(
            ["AAPL"], gaps
        )

        # Slice 921: a pass is two phases over the same symbol. The TRAILING
        # phase seeds and takes one chunk; the BACKFILL phase does not seed and
        # takes the rest, so the three chunks are split 1 + 2 across the pass.
        assert update_mock.call_count == 1, "only the trailing phase seeds"
        assert advance_mock.call_count == 3
        assert coalesce_mock.call_count == 2, "one per phase"
        # trailing: 1 gap; backfill: 2 gaps + the None that terminates it.
        assert pick_mock.call_count == 4

    def test_coalesce_called_after_each_phases_chunk_loop(self) -> None:
        gaps = [_gap(_dt(2024, 1, 1), _dt(2024, 3, 31)), None]
        _, _, _, coalesce_mock, _ = self._run(["AAPL"], gaps)
        # One per phase (slice 921): the trailing walk takes the gap, the
        # backfill walk finds none, and both coalesce after their loop.
        assert coalesce_mock.call_count == 2

    def test_success_count(self) -> None:
        report, _, _, _, _ = self._run(
            ["AAPL"], [None], outcome=LastAttemptOutcome.SUCCESS
        )
        # The symbol is walked once per phase (slice 921), so a single-symbol
        # pass records it twice.
        assert report.success_count == 2

    def test_multiple_symbols_each_get_own_gap_loop(self) -> None:
        # Two symbols; each gets None immediately
        gaps = [None, None, None, None]
        report, _, update_mock, coalesce_mock, _ = self._run(["AAPL", "MSFT"], gaps)
        # Each symbol seeds once, in the trailing phase only (slice 921).
        assert update_mock.call_count == 2
        # Two symbols x two phases.
        assert coalesce_mock.call_count == 4

    def test_seed_progress_accumulates_gaps_seeded_across_symbols(self, caplog) -> None:
        """slice 162: seed-phase progress sums gaps_inserted across all symbols
        and emits a completion INFO line with the accumulated total."""
        import logging

        gaps = [None] * 6
        with caplog.at_level(
            logging.INFO, logger="manta_trading.data.acquisition.daemon.minute"
        ):
            self._run(["AAPL", "MSFT", "GOOG"], gaps, gaps_inserted=3)

        # Slice 921: the completion line is per phase and names it. Seeding
        # happens in the trailing phase only, so that is where the total lands.
        trailing_lines = [
            r.message
            for r in caplog.records
            if "minute trailing: complete" in r.message
        ]
        assert len(trailing_lines) == 1
        assert "3 symbols" in trailing_lines[0]
        assert "9 gap rows seeded" in trailing_lines[0]

        backfill_lines = [
            r.message
            for r in caplog.records
            if "minute backfill: complete" in r.message
        ]
        assert len(backfill_lines) == 1
        assert "0 gap rows seeded" in backfill_lines[0]

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
        gaps_seeded = result.gaps_seeded
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
            call for call in mock_logger.info.call_args_list if "cycle" in call.args
        ]
        assert via_calls, "no INFO line carried via='cycle' on the happy path"


class TestViaMarkerThreading:
    """Tests for the via=refetch|cycle log marker added in slice 165."""

    def test_process_minute_symbol_forwards_via_to_do_minute_symbol(self) -> None:
        from manta_trading.data.acquisition.daemon.minute import (
            _process_minute_symbol,
        )

        mock_do = MagicMock(
            return_value=MinuteSymbolResult(
                LastAttemptOutcome.SUCCESS, None, None, 0, 0
            )
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
            result = _process_minute_symbol(
                "AAPL",
                pool=MagicMock(),
                http=MagicMock(),
                settings=_FakeSettings(),
                via=FetchEntryPoint.REFETCH,
            )
        assert result.outcome == LastAttemptOutcome.TRANSIENT_FAILURE
        # An unknown fault is not evidence about the provider (slice 921), so
        # the last-resort handler must not tag it PROVIDER and trip the breaker.
        assert result.failure_kind is MinuteFailureKind.DATABASE
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
        mock_do_minute = MagicMock(
            return_value=MinuteSymbolResult(outcome, None, None, 0, 0)
        )
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
        # Slice 921: the range end is the session close, so the diff needs the
        # open->close mapping as well (09:30-16:00 ET == 13:30-20:00 UTC EDT).
        session_closes = {
            session: session.replace(hour=20, minute=0) for session in sessions
        }
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
            # Real compute_missing_minute_sessions runs; only clamp_to_lifecycle,
            # fetch_sessions and fetch_session_bounds (its DB I/O boundary) are
            # patched.
            patch(
                "manta_trading.data.gaps.minute_coverage.clamp_to_lifecycle",
                return_value=(history_start, target_end),
            ),
            patch(
                "manta_trading.data.gaps.minute_coverage.fetch_sessions",
                return_value=sessions,
            ),
            patch(
                "manta_trading.data.gaps.minute_coverage.fetch_session_bounds",
                return_value=session_closes,
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

        assert result.gaps_seeded == 0  # reported via the update_data_gaps mock
        first_call_kwargs = mock_update_gaps.call_args_list[0].kwargs
        seeded_ranges = first_call_kwargs["precomputed_ranges"]
        assert seeded_ranges is not None
        assert len(seeded_ranges) == 1
        # The recreated hole spans the missing session open to its close (921).
        assert seeded_ranges[0].gap_start_utc == _dt(2024, 6, 11)
        assert seeded_ranges[0].gap_end_utc == session_closes[_dt(2024, 6, 11)]
        # Never the legacy full-history span
        assert not any(
            r.gap_start_utc == history_start and r.gap_end_utc == target_end
            for r in seeded_ranges
        )


# ---------------------------------------------------------------------------
# Issue #19: terminal-only gap rows must not freeze trailing seeding
# ---------------------------------------------------------------------------


class TestTrailingSeedAfterTerminalGaps:
    """Pins the issue-#19 fix: a symbol with bars whose gap rows are ALL
    terminal (PROVIDER_HOLE / RETRY_EXHAUSTED, no UNKNOWN) was never
    re-seeded, so its minute data froze at the last fetch (production
    2026-08-31: ~7,300 active symbols). The gate now seeds the uncovered
    trailing window — strictly from the gap frontier (MAX(gap_end)) — and
    never the full history window, so terminal markers behind the frontier
    stay out of update_data_gaps' containment delete.
    """

    _HISTORY_START = datetime(2004, 1, 1, tzinfo=UTC)

    def _run_gate(
        self,
        preflight_row: tuple,
        coverage_index: dict | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        """Drive _do_minute_symbol past the seed gate with a controlled
        preflight row (has_bars, has_unknown_gaps, has_any_gaps,
        gap_frontier). Returns the update_data_gaps and
        compute_missing_minute_sessions mocks.
        """
        mock_update_gaps = MagicMock(return_value=MagicMock(gaps_inserted=0))
        mock_compute = MagicMock(return_value=[])

        cur = MagicMock()
        cur.fetchone.return_value = preflight_row
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
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

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.update_data_gaps",
                mock_update_gaps,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute."
                "compute_missing_minute_sessions",
                mock_compute,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute."
                "_resolve_minute_history_start",
                return_value=self._HISTORY_START,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.advisory_lock",
                return_value=lock_cm,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute."
                "pick_most_recent_actionable_gap",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
            ),
        ):
            _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                via=FetchEntryPoint.CYCLE,
                coverage_index=coverage_index,
            )
        return mock_update_gaps, mock_compute

    def test_terminal_only_rows_seed_the_trailing_window_from_the_frontier(
        self,
    ) -> None:
        frontier = _dt(2026, 8, 26)
        upd, comp = self._run_gate(
            (True, False, True, frontier), coverage_index={"AAPL": set()}
        )
        assert upd.call_count == 1
        # from_ts is the frontier — never history_start, which would put every
        # terminal row behind it inside the seed's containment delete.
        assert upd.call_args.args[3] == frontier
        comp.assert_called_once()
        assert comp.call_args.args[3] == frontier

    def test_frontier_at_target_end_seeds_nothing(self) -> None:
        far_future = datetime(2999, 1, 1, tzinfo=UTC)
        upd, comp = self._run_gate((True, False, True, far_future))
        upd.assert_not_called()
        comp.assert_not_called()

    def test_unknown_gaps_still_seed_from_history_start(self) -> None:
        upd, _ = self._run_gate((True, True, True, _dt(2026, 8, 26)))
        assert upd.call_args.args[3] == self._HISTORY_START

    def test_no_gap_rows_still_seed_from_history_start(self) -> None:
        upd, _ = self._run_gate((True, False, False, None))
        assert upd.call_args.args[3] == self._HISTORY_START

    def test_no_bars_still_seed_from_history_start(self) -> None:
        upd, _ = self._run_gate((False, False, False, None))
        assert upd.call_args.args[3] == self._HISTORY_START

    # --- Slice 921 Task 1.7: the frontier gate against a session-close end ---

    def test_session_close_frontier_still_triggers_the_trailing_seed(self) -> None:
        """The gate is ``MAX(gap_end) < target_end`` with target_end = today's
        UTC midnight. A minute row now ends at its session close, so the
        frontier is 20:00 (summer) or 21:00 (winter) on a past session date —
        still strictly below today's midnight, so the trailing seed fires
        exactly as it did when the frontier sat at the session open."""
        for label, frontier in (
            ("summer close", datetime(2026, 7, 15, 20, 0, tzinfo=UTC)),
            ("winter close", datetime(2026, 1, 15, 21, 0, tzinfo=UTC)),
        ):
            upd, comp = self._run_gate(
                (True, False, True, frontier), coverage_index={"AAPL": set()}
            )
            assert upd.call_count == 1, label
            assert upd.call_args.args[3] == frontier, label
            comp.assert_called_once()
            assert comp.call_args.args[3] == frontier, label


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


# ---------------------------------------------------------------------------
# Slice 921 — the provider window reaches the end of the day (Task 1.5, SC1)
# ---------------------------------------------------------------------------


class TestProviderWindowReachesDayEnd:
    """The EODHD request's ``to`` is the UTC midnight AFTER ``chunk_end``'s date.

    EODHD honors ``to`` exactly, and 1-minute bars exist for extended hours
    (AAPL traded 08:00-23:59 UTC on 2026-08-27). A request ending at the
    20:00 session close would drop the pre- and post-market bars the
    midnight-anchored legacy rows collected, so the fetch layer widens the
    request while ``chunk_end`` and everything downstream of it stay put.
    """

    @staticmethod
    def _capture(gap: GapRow) -> tuple[list[str], MagicMock]:
        """Run one chunk of ``_do_minute_symbol``, returning (urls, classify_mock)."""
        urls: list[str] = []
        gap_iter = iter([gap, None])

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

        response = MagicMock(status_code=200, json=MagicMock(return_value=[]))

        def _fake_get(_http, url, _call_type):
            urls.append(url)
            return response

        classify = MagicMock(return_value=LastAttemptOutcome.SUCCESS)
        resolved_start = datetime(2004, 1, 1, tzinfo=UTC)

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.eodhd_get",
                side_effect=_fake_get,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.classify_outcome",
                classify,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.outcome_to_fetch_status",
                return_value=None,
            ),
            patch("manta_trading.data.acquisition.daemon.minute.update_data_gaps"),
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
                return_value=0,
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
        ):
            _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                via=FetchEntryPoint.CYCLE,
                window=None,
                coverage_index=None,
            )
        return urls, classify

    @staticmethod
    def _epochs(url: str) -> tuple[int, int]:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        return int(query["from"][0]), int(query["to"][0])

    def test_trailing_single_session_requests_through_the_day_end(self) -> None:
        # One session, ending at its 20:00 UTC close (slice 921 range end).
        chunk_start = datetime(2026, 9, 3, 13, 30, tzinfo=UTC)
        chunk_end = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)
        urls, _ = self._capture(_gap(chunk_start, chunk_end))
        assert len(urls) == 1
        from_epoch, to_epoch = self._epochs(urls[0])
        assert from_epoch == int(chunk_start.timestamp())
        assert to_epoch == int(datetime(2026, 9, 4, tzinfo=UTC).timestamp())

    def test_multi_day_chunk_requests_through_the_last_day_end(self) -> None:
        chunk_start = datetime(2026, 9, 1, 13, 30, tzinfo=UTC)
        chunk_end = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)
        urls, _ = self._capture(_gap(chunk_start, chunk_end))
        from_epoch, to_epoch = self._epochs(urls[0])
        assert from_epoch == int(chunk_start.timestamp())
        assert to_epoch == int(datetime(2026, 9, 4, tzinfo=UTC).timestamp())

    def test_chunk_end_already_at_midnight_does_not_add_a_second_day(self) -> None:
        # Legacy midnight-anchored rows must not gain an extra calendar day.
        chunk_start = datetime(2026, 9, 3, tzinfo=UTC)
        chunk_end = datetime(2026, 9, 4, tzinfo=UTC)
        urls, _ = self._capture(_gap(chunk_start, chunk_end))
        _, to_epoch = self._epochs(urls[0])
        assert to_epoch == int(chunk_end.timestamp())

    def test_classify_outcome_receives_the_unextended_range(self) -> None:
        """The widened request must not shift classification semantics."""
        chunk_start = datetime(2026, 9, 3, 13, 30, tzinfo=UTC)
        chunk_end = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)
        _, classify = self._capture(_gap(chunk_start, chunk_end))
        classify.assert_called_once()
        _response, classified_start, classified_end = classify.call_args.args
        assert classified_start == chunk_start
        assert classified_end == chunk_end


class TestDayEndUtc:
    """``day_end_utc`` in isolation — the named helper the URL uses."""

    def test_intraday_moment_rolls_to_the_next_midnight(self) -> None:
        from manta_trading.data.acquisition.daemon.minute import day_end_utc

        assert day_end_utc(datetime(2026, 9, 3, 20, 0, tzinfo=UTC)) == datetime(
            2026, 9, 4, tzinfo=UTC
        )

    def test_midnight_is_returned_unchanged(self) -> None:
        from manta_trading.data.acquisition.daemon.minute import day_end_utc

        midnight = datetime(2026, 9, 4, tzinfo=UTC)
        assert day_end_utc(midnight) == midnight

    def test_non_utc_input_is_normalised_before_the_day_boundary(self) -> None:
        from datetime import timedelta as _timedelta

        from manta_trading.data.acquisition.daemon.minute import day_end_utc

        # 2026-09-03 21:00-04:00 == 2026-09-04 01:00 UTC, so the UTC day end
        # is 2026-09-05, not 2026-09-04.
        eastern = timezone(-_timedelta(hours=4))
        moment = datetime(2026, 9, 3, 21, 0, tzinfo=eastern)
        assert day_end_utc(moment) == datetime(2026, 9, 5, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Slice 921 Section 2 — no response, no accounting (Task 2.4, SC4)
# ---------------------------------------------------------------------------


class TestAccountingMovesOnlyOnAProviderAnswer:
    """``attempt_count``, ``fetch_status`` and ``acquisition_state`` move only
    when the provider actually answered.

    ``classify_outcome`` RETURNS ``TRANSIENT_FAILURE`` (it does not raise) for
    HTTP 429, any 5xx, an unparseable body, and EODHD's 200-with-``{"error":
    …}`` quirk. The chunk loop used to call ``_advance_minute_gap`` and
    ``_record_minute_attempt`` for those, consuming a retry and — at
    MAX_RETRY_COUNT — promoting a perfectly live gap to RETRY_EXHAUSTED
    without the provider ever having said anything about the range.
    """

    @staticmethod
    def _run(response: MagicMock) -> tuple[MagicMock, MagicMock, int]:
        """Drive one chunk with ``response``.

        Returns (_advance_minute_gap mock, _record_minute_attempt mock,
        number of provider calls made).
        """
        gap = _gap(
            datetime(2026, 9, 3, 13, 30, tzinfo=UTC),
            datetime(2026, 9, 3, 20, 0, tzinfo=UTC),
        )
        # A long gap sequence: if the loop does NOT break, it would keep
        # requesting chunks, which the call count below detects.
        gap_iter = iter([gap] * 6 + [None])
        calls: list[str] = []

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

        advance = MagicMock(return_value=None)
        record = MagicMock(return_value=None)

        def _fake_get(_http, url, _call_type):
            calls.append(url)
            return response

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.eodhd_get",
                side_effect=_fake_get,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._advance_minute_gap",
                advance,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._record_minute_attempt",
                record,
            ),
            patch("manta_trading.data.acquisition.daemon.minute.update_data_gaps"),
            patch(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                return_value=datetime(2004, 1, 1, tzinfo=UTC),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
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
        ):
            _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                via=FetchEntryPoint.CYCLE,
                window=None,
                coverage_index=None,
            )
        return advance, record, len(calls)

    @staticmethod
    def _response(status_code: int, body: object = None) -> MagicMock:
        response = MagicMock(status_code=status_code)
        if isinstance(body, Exception):
            response.json = MagicMock(side_effect=body)
        else:
            response.json = MagicMock(return_value=body)
        return response

    # --- No answer: accounting must not move -------------------------------

    def test_http_500_writes_no_accounting(self) -> None:
        advance, record, _ = self._run(self._response(500, {"error": "boom"}))
        advance.assert_not_called()
        record.assert_not_called()

    def test_http_503_writes_no_accounting(self) -> None:
        advance, record, _ = self._run(self._response(503, None))
        advance.assert_not_called()
        record.assert_not_called()

    def test_http_429_writes_no_accounting(self) -> None:
        """429 after the quota bucket has already backed off — the provider
        still has not answered anything about this range."""
        advance, record, _ = self._run(self._response(429, None))
        advance.assert_not_called()
        record.assert_not_called()

    def test_unparseable_body_writes_no_accounting(self) -> None:
        """A read timeout / connection reset surfaces here as a body that will
        not parse; there is no answer to record."""
        advance, record, _ = self._run(
            self._response(200, ValueError("Expecting value: line 1 column 1"))
        )
        advance.assert_not_called()
        record.assert_not_called()

    def test_two_hundred_with_error_body_writes_no_accounting(self) -> None:
        """EODHD's 200-with-{"error": …} quirk is a failure wearing a 200.

        It is a *classified* response but carries no statement about the
        range, so it is treated as no-answer: the gap row keeps its status and
        count for the next pass, rather than burning a retry on a response
        that contains no data and no denial.
        """
        advance, record, _ = self._run(
            self._response(200, {"error": "Symbol not found or API limit"})
        )
        advance.assert_not_called()
        record.assert_not_called()

    def test_a_no_answer_failure_ends_the_symbols_chunk_loop(self) -> None:
        """One request, then stop — there is no point asking the next chunk of
        a provider that just failed to answer this one."""
        _, _, provider_calls = self._run(self._response(500, None))
        assert provider_calls == 1

    def test_http_402_aborts_before_any_accounting(self) -> None:
        """402 (daily allowance spent) already RAISES ProviderResponseError in
        classify_outcome, so it never reaches the accounting calls. Pinned
        here so SC4's list of failure modes is complete in one place."""
        from manta_trading.data.acquisition.outcomes import ProviderResponseError

        with pytest.raises(ProviderResponseError, match="quota exhausted"):
            self._run(self._response(402, None))

    # --- A real answer: accounting must move exactly as before -------------

    def test_classified_two_hundred_still_moves_accounting(self) -> None:
        body = [
            {
                "timestamp": 1772798400,
                "datetime": "2026-09-03 19:59:00",
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "1000",
            }
        ]
        advance, record, _ = self._run(self._response(200, body))
        advance.assert_called()
        record.assert_called()

    def test_empty_list_body_still_moves_accounting(self) -> None:
        """An empty list is a real answer: the provider says there is nothing
        for this range, which is what PROVIDER_HOLE records."""
        advance, record, _ = self._run(self._response(200, []))
        advance.assert_called()
        record.assert_called()

    def test_http_404_still_moves_accounting(self) -> None:
        """EODHD's documented "no intraday data for this symbol" — an answer."""
        advance, record, _ = self._run(self._response(404, None))
        advance.assert_called()
        record.assert_called()


class TestRealFetchFailuresStillReachExhaustion:
    """The safety valve survives slice 921's accounting fix.

    Removing the seed's phantom increment (and skipping accounting for
    un-answered responses) must not make RETRY_EXHAUSTED unreachable — a range
    the provider repeatedly ANSWERS but cannot fill still has to stop being
    retried forever. ``_advance_minute_gap`` is the surviving incrementer and
    promoter, and it now runs only behind a real answer.
    """

    @staticmethod
    def _advance(attempt_count: int, outcome: LastAttemptOutcome) -> list[tuple]:
        from manta_trading.data.acquisition.daemon.minute import _advance_minute_gap
        from manta_trading.data.acquisition.outcomes import outcome_to_fetch_status

        gap_start = datetime(2026, 9, 3, 13, 30, tzinfo=UTC)
        gap_end = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)
        picked = GapRow(
            symbol="AAPL",
            granularity="minute",
            gap_start=gap_start,
            gap_end=gap_end,
            fetch_status=str(FetchStatus.UNKNOWN),
            last_attempt_ts=None,
            attempt_count=attempt_count,
        )
        executes: list[tuple] = []
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.execute = MagicMock(
            side_effect=lambda sql, params=(): executes.append((sql, params))
        )
        conn = MagicMock()
        conn.cursor = MagicMock(return_value=cur)

        _advance_minute_gap(
            conn,
            picked=picked,
            chunk_start=gap_start,
            chunk_end=gap_end,
            outcome=outcome,
            fetch_status=outcome_to_fetch_status(outcome),
        )
        return executes

    def test_an_answered_partial_still_increments_attempt_count(self) -> None:
        executes = self._advance(1, LastAttemptOutcome.PARTIAL)
        updates = [p for sql, p in executes if "UPDATE data_gaps" in sql]
        assert len(updates) == 1
        # (fetch_status, last_attempt_ts, attempt_count, ...)
        assert updates[0][2] == 2, "a real answer still advances the count"

    def test_repeated_answered_failures_still_promote_to_retry_exhausted(
        self,
    ) -> None:
        """At MAX_RETRY_COUNT the row goes terminal, exactly as before —
        the difference is that every one of those attempts was answered."""
        executes = self._advance(MAX_RETRY_COUNT - 1, LastAttemptOutcome.PARTIAL)
        updates = [p for sql, p in executes if "UPDATE data_gaps" in sql]
        assert len(updates) == 1
        assert updates[0][2] == MAX_RETRY_COUNT
        assert updates[0][0] == str(FetchStatus.RETRY_EXHAUSTED)


# ---------------------------------------------------------------------------
# Slice 921 Section 3 — trailing then backfill (Task 3.5, SC6)
# ---------------------------------------------------------------------------


class TestMinutePassPhases:
    """A minute pass attempts every symbol's current session before it
    requests a single backfill chunk.

    The failure this prevents: on 2026-09-07 the 13:05 UTC pass walked the
    universe once in ``most_stale_first`` order, spent its whole run on deep
    backfill, and never reached the current session — so the nightly minute
    data simply did not arrive.
    """

    @staticmethod
    def _run_cycle(
        symbols: list[str],
        gaps_by_symbol: Mapping[str, Sequence[GapRow | None]],
        *,
        should_continue=None,
    ) -> tuple[list[str], list[tuple], MagicMock]:
        """Run a full pass, recording the (symbol, min_gap_end, max_chunks,
        seed) of every _process_minute_symbol call and every requested URL.

        Returns (requested_urls, phase_calls, update_data_gaps_mock).
        """
        urls: list[str] = []
        phase_calls: list[tuple] = []
        pending = {sym: list(gaps) for sym, gaps in gaps_by_symbol.items()}

        real_process = None

        def _fake_pick(_conn, symbol, _gran, _from, _to, min_gap_end=None):
            queue = pending.get(symbol, [])
            while queue:
                gap = queue.pop(0)
                if gap is None:
                    return None
                if min_gap_end is not None and gap.gap_end < min_gap_end:
                    # Outside the trailing floor — put it back for backfill.
                    queue.insert(0, gap)
                    return None
                return gap
            return None

        def _fake_get(_http, url, _call_type):
            urls.append(url)
            return MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value=[
                        {
                            "timestamp": 1704196200,
                            "datetime": "2026-09-08 19:59:00",
                            "open": "100",
                            "high": "101",
                            "low": "99",
                            "close": "100",
                            "volume": "1000",
                        }
                    ]
                ),
            )

        with ExitStack() as stack:

            def mp(target: str, **kwargs) -> MagicMock:
                return stack.enter_context(patch(target, **kwargs))

            mp(
                "manta_trading.data.acquisition.daemon.minute.Settings",
                return_value=_FakeSettings(),
            )
            update_mock = mp(
                "manta_trading.data.acquisition.daemon.minute.update_data_gaps",
                return_value=MagicMock(gaps_inserted=0),
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.build_minute_coverage_index",
                return_value={},
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.compute_missing_minute_sessions",
                return_value=[],
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                return_value=datetime(2004, 1, 1, tzinfo=UTC),
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute._advance_minute_gap",
                return_value=None,
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute._record_minute_attempt",
                return_value=None,
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
            )
            mp("manta_trading.data.acquisition.daemon.minute._insert_minute_bars")
            mp(
                "manta_trading.data.acquisition.daemon.minute.eodhd_get",
                side_effect=_fake_get,
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.pick_most_recent_actionable_gap",
                side_effect=_fake_pick,
            )

            lock_cm = MagicMock()
            lock_cm.__enter__ = MagicMock(return_value=None)
            lock_cm.__exit__ = MagicMock(return_value=False)
            mp(
                "manta_trading.data.acquisition.daemon.minute.advisory_lock",
                return_value=lock_cm,
            )

            # Record the knobs each phase passes, then run the real function.
            from manta_trading.data.acquisition.daemon import minute as minute_mod

            real_process = minute_mod._process_minute_symbol

            def _spy(symbol, **kwargs):
                phase_calls.append(
                    (
                        symbol,
                        kwargs.get("min_gap_end"),
                        kwargs.get("max_chunks"),
                        kwargs.get("seed"),
                    )
                )
                return real_process(symbol, **kwargs)

            mp(
                "manta_trading.data.acquisition.daemon.minute._process_minute_symbol",
                side_effect=_spy,
            )

            pool_cls = mp("manta_trading.data.acquisition.daemon.minute.ConnectionPool")
            http_cls = mp("manta_trading.data.acquisition.daemon.minute.httpx.Client")
            pool = MagicMock()
            pool_cls.return_value.__enter__ = MagicMock(return_value=pool)
            pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            conn = MagicMock()
            txn = MagicMock()
            txn.__enter__ = MagicMock(return_value=txn)
            txn.__exit__ = MagicMock(return_value=False)
            conn.transaction.return_value = txn
            cur = MagicMock()
            cur.__enter__ = MagicMock(return_value=cur)
            cur.__exit__ = MagicMock(return_value=False)
            # (has_bars, has_unknown_gaps, has_any_gaps, gap_frontier)
            cur.fetchone.return_value = (True, True, True, None)
            conn.cursor.return_value = cur
            pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
            pool.connection.return_value.__exit__ = MagicMock(return_value=False)
            http = MagicMock()
            http_cls.return_value.__enter__ = MagicMock(return_value=http)
            http_cls.return_value.__exit__ = MagicMock(return_value=False)

            run_minute_cycle(symbols=symbols, should_continue=should_continue)

        return urls, phase_calls, update_mock

    @staticmethod
    def _trailing_gap() -> GapRow:
        """A gap inside MINUTE_TRAILING_PRIORITY_WINDOW."""
        end = datetime.now(UTC) - timedelta(days=1)
        return GapRow(
            symbol="AAPL",
            granularity="minute",
            gap_start=end - timedelta(hours=7),
            gap_end=end,
            fetch_status="UNKNOWN",
            last_attempt_ts=None,
            attempt_count=0,
        )

    @staticmethod
    def _old_gap() -> GapRow:
        """A gap well outside the trailing window."""
        end = datetime.now(UTC) - timedelta(days=400)
        return GapRow(
            symbol="AAPL",
            granularity="minute",
            gap_start=end - timedelta(hours=7),
            gap_end=end,
            fetch_status="UNKNOWN",
            last_attempt_ts=None,
            attempt_count=0,
        )

    def test_every_trailing_request_precedes_every_backfill_request(self) -> None:
        """SC6: no backfill chunk is requested until every active symbol's
        trailing gap has been attempted."""
        gaps = {
            "AAPL": [self._trailing_gap(), self._old_gap(), None],
            "MSFT": [self._trailing_gap(), self._old_gap(), None],
        }
        _, phase_calls, _ = self._run_cycle(["AAPL", "MSFT"], gaps)
        phases = [
            "trailing" if max_chunks is not None else "backfill"
            for _sym, _floor, max_chunks, _seed in phase_calls
        ]
        # Every trailing call comes before every backfill call.
        assert phases == ["trailing", "trailing", "backfill", "backfill"]

    def test_the_trailing_phase_passes_the_window_floor(self) -> None:
        gaps = {"AAPL": [self._trailing_gap(), None]}
        _, phase_calls, _ = self._run_cycle(["AAPL"], gaps)
        trailing = [c for c in phase_calls if c[2] is not None]
        assert len(trailing) == 1
        floor = trailing[0][1]
        assert floor is not None
        expected = datetime.now(UTC) - MINUTE_TRAILING_PRIORITY_WINDOW
        assert abs((floor - expected).total_seconds()) < 60

    def test_the_backfill_phase_passes_no_floor_and_no_chunk_bound(self) -> None:
        gaps = {"AAPL": [self._trailing_gap(), self._old_gap(), None]}
        _, phase_calls, _ = self._run_cycle(["AAPL"], gaps)
        backfill = [c for c in phase_calls if c[2] is None]
        assert len(backfill) == 1
        _sym, floor, max_chunks, _seed = backfill[0]
        assert floor is None
        assert max_chunks is None

    def test_the_trailing_phase_takes_at_most_one_chunk_per_symbol(self) -> None:
        """Even with several actionable trailing gaps, the trailing walk takes
        one chunk and moves on — a single symbol's backlog must not delay the
        current session of every symbol behind it."""
        several = [self._trailing_gap() for _ in range(4)]
        gaps = {"AAPL": [*several, None], "MSFT": [self._trailing_gap(), None]}
        _, phase_calls, _ = self._run_cycle(["AAPL", "MSFT"], gaps)
        trailing = [c for c in phase_calls if c[2] is not None]
        assert all(
            max_chunks == MINUTE_TRAILING_MAX_CHUNKS_PER_SYMBOL
            for _s, _f, max_chunks, _seed in trailing
        )

    def test_only_the_trailing_phase_seeds(self) -> None:
        """Task 3.3 option (a): the backfill walk consumes existing rows only,
        so no symbol-day can be requested twice in one pass."""
        gaps = {"AAPL": [self._trailing_gap(), self._old_gap(), None]}
        _, phase_calls, update_mock = self._run_cycle(["AAPL"], gaps)
        seeds = [seed for _s, _f, _m, seed in phase_calls]
        assert seeds == [True, False]
        # The seed itself ran exactly once for the symbol, in the trailing walk.
        assert update_mock.call_count == 1

    def test_a_stale_coverage_index_cannot_cause_a_second_fetch(self) -> None:
        """The direct Task 3.3 regression.

        Fixture: a symbol whose trailing session was just fetched, with a
        coverage index that (as at cycle start) still reports that day as
        uncovered. Because the backfill phase does not seed, the day is never
        re-inserted and never re-requested — the guarantee holds by
        construction, not by the index happening to be fresh.
        """
        trailing = self._trailing_gap()
        gaps = {"AAPL": [trailing, None]}
        urls, phase_calls, update_mock = self._run_cycle(["AAPL"], gaps)
        # Exactly one provider request for the trailing session.
        assert len(urls) == 1
        # The backfill phase ran, and seeded nothing.
        assert [seed for _s, _f, _m, seed in phase_calls] == [True, False]
        assert update_mock.call_count == 1

    def test_trailing_phase_complete_is_logged_before_any_backfill_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SC6's second half, and what the design's Verification Walkthrough
        greps for — the operator's only in-production evidence that the
        trailing phase ran to completion."""
        import logging

        gaps = {
            "AAPL": [self._trailing_gap(), self._old_gap(), None],
            "MSFT": [self._trailing_gap(), None],
        }
        with caplog.at_level(
            logging.INFO, logger="manta_trading.data.acquisition.daemon.minute"
        ):
            self._run_cycle(["AAPL", "MSFT"], gaps)

        messages = [r.message for r in caplog.records]
        complete = [
            i for i, m in enumerate(messages) if "trailing phase complete:" in m
        ]
        assert len(complete) == 1
        assert "2 symbols" in messages[complete[0]]

        backfill_lines = [i for i, m in enumerate(messages) if "minute backfill" in m]
        assert backfill_lines, "the backfill phase must log"
        assert complete[0] < min(backfill_lines)

    def test_a_shutdown_in_the_trailing_phase_skips_backfill_entirely(self) -> None:
        """A shutdown request ends the PASS. Starting the backfill walk would
        issue requests the operator already asked the process to stop."""
        polls = {"n": 0}

        def flag() -> bool:
            polls["n"] += 1
            return polls["n"] <= 1

        gaps = {"AAPL": [self._trailing_gap(), None], "MSFT": [None]}
        _, phase_calls, _ = self._run_cycle(
            ["AAPL", "MSFT"], gaps, should_continue=flag
        )
        assert all(max_chunks is not None for _s, _f, max_chunks, _seed in phase_calls)


# ---------------------------------------------------------------------------
# Slice 921 Section 4 — the failure kind survives (Task 4.2)
# ---------------------------------------------------------------------------


class TestFailureKindSurvivesProcessMinuteSymbol:
    """A PoolTimeout, an httpx.ReadTimeout and a RETURNED HTTP 500 must be
    distinguishable by the cycle without inspecting an exception message.

    The 500 is the case a handler-only reading of this misses:
    ``classify_outcome`` RETURNS TRANSIENT_FAILURE for 5xx and 429, so the
    symbol exits ``_do_minute_symbol`` normally and never touches an
    ``except`` clause. Without the kind on the normal return path the
    breaker's headline trigger would never reach the cycle at all.
    """

    @staticmethod
    def _process_with(side_effect) -> MinuteSymbolResult:
        from manta_trading.data.acquisition.daemon.minute import (
            _process_minute_symbol,
        )

        with patch(
            "manta_trading.data.acquisition.daemon.minute._do_minute_symbol",
            side_effect=side_effect,
        ):
            return _process_minute_symbol(
                "AAPL",
                pool=MagicMock(),
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                via=FetchEntryPoint.CYCLE,
            )

    def test_pool_timeout_is_a_database_kind(self) -> None:
        result = self._process_with(PoolTimeout("pool exhausted"))
        assert result.failure_kind is MinuteFailureKind.DATABASE

    def test_lock_timeout_is_a_database_kind(self) -> None:
        result = self._process_with(psycopg.errors.LockNotAvailable("locked"))
        assert result.failure_kind is MinuteFailureKind.DATABASE

    def test_read_timeout_is_a_provider_kind(self) -> None:
        result = self._process_with(httpx.ReadTimeout("timed out"))
        assert result.failure_kind is MinuteFailureKind.PROVIDER

    def test_quota_exhaustion_is_its_own_kind(self) -> None:
        from manta_trading.data.acquisition.outcomes import ProviderQuotaExhausted

        result = self._process_with(ProviderQuotaExhausted("402"))
        assert result.failure_kind is MinuteFailureKind.PROVIDER_QUOTA

    def test_other_four_xx_is_a_provider_kind(self) -> None:
        from manta_trading.data.acquisition.outcomes import ProviderResponseError

        result = self._process_with(ProviderResponseError("418 teapot"))
        assert result.failure_kind is MinuteFailureKind.PROVIDER

    def test_a_returned_five_hundred_is_a_provider_kind(self) -> None:
        """The normal-return case — no exception is raised anywhere."""
        response = MagicMock(status_code=500, json=MagicMock(return_value=None))
        gap = _gap(
            datetime(2026, 9, 3, 13, 30, tzinfo=UTC),
            datetime(2026, 9, 3, 20, 0, tzinfo=UTC),
        )
        gap_iter = iter([gap, None])
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

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.eodhd_get",
                return_value=response,
            ),
            patch("manta_trading.data.acquisition.daemon.minute.update_data_gaps"),
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
                return_value=datetime(2004, 1, 1, tzinfo=UTC),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
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
        ):
            out = _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                via=FetchEntryPoint.CYCLE,
                window=None,
                coverage_index=None,
            )
        assert out.failure_kind is MinuteFailureKind.PROVIDER

    def test_a_classified_response_carries_no_failure_kind(self) -> None:
        """A symbol that reached a real answer resets the breaker."""
        result = self._process_with(
            [
                MinuteSymbolResult(
                    LastAttemptOutcome.SUCCESS,
                    None,
                    None,
                    1,
                    0,
                )
            ]
        )
        assert result.failure_kind is MinuteFailureKind.NONE


# ---------------------------------------------------------------------------
# Slice 921 Section 4 — abort and breaker (Task 4.5)
# ---------------------------------------------------------------------------


class TestPassAbortAndBreaker:
    """A pass stops when every further request would be wasted.

    Two conditions: the provider says the daily allowance is spent (HTTP 402),
    or MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES symbols in a row fail for
    a provider-side reason. A DATABASE-kind failure must do neither — a
    Postgres pool exhaustion is not evidence that EODHD is down.
    """

    @staticmethod
    def _run_pass(
        symbols: list[str],
        results_by_symbol: Mapping[str, MinuteSymbolResult],
    ) -> tuple[CycleReport, list[str]]:
        """Run a full pass with _process_minute_symbol stubbed per symbol.

        Returns (report, symbols_attempted_in_order).
        """
        attempted: list[str] = []

        def _fake_process(symbol: str, **_kwargs) -> MinuteSymbolResult:
            attempted.append(symbol)
            return results_by_symbol.get(
                symbol,
                MinuteSymbolResult(LastAttemptOutcome.SUCCESS, None, None, 1, 0),
            )

        with ExitStack() as stack:

            def mp(target: str, **kwargs) -> MagicMock:
                return stack.enter_context(patch(target, **kwargs))

            mp(
                "manta_trading.data.acquisition.daemon.minute.Settings",
                return_value=_FakeSettings(),
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute._process_minute_symbol",
                side_effect=_fake_process,
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.build_minute_coverage_index",
                return_value={},
            )
            pool_cls = mp("manta_trading.data.acquisition.daemon.minute.ConnectionPool")
            http_cls = mp("manta_trading.data.acquisition.daemon.minute.httpx.Client")
            pool = MagicMock()
            pool_cls.return_value.__enter__ = MagicMock(return_value=pool)
            pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            conn = MagicMock()
            pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
            pool.connection.return_value.__exit__ = MagicMock(return_value=False)
            http = MagicMock()
            http_cls.return_value.__enter__ = MagicMock(return_value=http)
            http_cls.return_value.__exit__ = MagicMock(return_value=False)

            report = run_minute_cycle(symbols=symbols)

        return report, attempted

    @staticmethod
    def _failure(kind: MinuteFailureKind) -> MinuteSymbolResult:
        return MinuteSymbolResult(
            LastAttemptOutcome.TRANSIENT_FAILURE, None, None, 0, 0, kind
        )

    def test_a_402_on_the_first_symbol_ends_the_pass(self) -> None:
        """SC5: no further provider call after the quota is reported spent."""
        symbols = [f"SYM{i}" for i in range(10)]
        report, attempted = self._run_pass(
            symbols, {"SYM0": self._failure(MinuteFailureKind.PROVIDER_QUOTA)}
        )
        assert attempted == ["SYM0"], "the pass must stop at the 402"
        assert report.minute_pass_outcome is MinutePassOutcome.QUOTA_EXHAUSTED

    def test_five_consecutive_provider_failures_end_the_pass(self) -> None:
        symbols = [f"SYM{i}" for i in range(20)]
        failing = {
            f"SYM{i}": self._failure(MinuteFailureKind.PROVIDER)
            for i in range(MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES)
        }
        report, attempted = self._run_pass(symbols, failing)
        assert len(attempted) == MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES
        assert report.minute_pass_outcome is MinutePassOutcome.PROVIDER_UNAVAILABLE

    def test_five_consecutive_database_failures_do_not_end_the_pass(self) -> None:
        """The misattribution regression. A Postgres pool exhaustion is not
        evidence about the provider; aborting on it would stop collecting data
        because of our own contention."""
        symbols = [f"SYM{i}" for i in range(10)]
        failing = {
            f"SYM{i}": self._failure(MinuteFailureKind.DATABASE)
            for i in range(MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES)
        }
        report, attempted = self._run_pass(symbols, failing)
        # Both phases walked all ten symbols.
        assert len(attempted) == 20
        assert report.minute_pass_outcome is MinutePassOutcome.COMPLETE

    def test_scattered_failures_never_trip_the_breaker(self) -> None:
        """Four failures, a classified answer, then four more — the streak
        resets, so a long pass with intermittent trouble still completes."""
        symbols = [f"SYM{i}" for i in range(10)]
        cap = MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES
        failing = {
            f"SYM{i}": self._failure(MinuteFailureKind.PROVIDER)
            for i in list(range(cap - 1)) + list(range(cap, 2 * cap - 1))
        }
        # SYM4 (index cap-1) is left as the default SUCCESS, resetting the run.
        report, attempted = self._run_pass(symbols, failing)
        assert len(attempted) == 20
        assert report.minute_pass_outcome is MinutePassOutcome.COMPLETE

    def test_a_database_failure_does_not_reset_the_streak(self) -> None:
        """Only a real provider answer clears it. A DB fault is silent
        evidence — neither for nor against the provider being up."""
        symbols = [f"SYM{i}" for i in range(20)]
        results = {
            "SYM0": self._failure(MinuteFailureKind.PROVIDER),
            "SYM1": self._failure(MinuteFailureKind.PROVIDER),
            "SYM2": self._failure(MinuteFailureKind.DATABASE),
            "SYM3": self._failure(MinuteFailureKind.PROVIDER),
            "SYM4": self._failure(MinuteFailureKind.PROVIDER),
            "SYM5": self._failure(MinuteFailureKind.PROVIDER),
        }
        report, attempted = self._run_pass(symbols, results)
        assert report.minute_pass_outcome is MinutePassOutcome.PROVIDER_UNAVAILABLE
        assert len(attempted) == 6

    def test_the_outcome_carries_the_phase_it_aborted_in(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        symbols = [f"SYM{i}" for i in range(10)]
        with caplog.at_level(
            logging.ERROR, logger="manta_trading.data.acquisition.daemon.minute"
        ):
            report, _ = self._run_pass(
                symbols, {"SYM0": self._failure(MinuteFailureKind.PROVIDER_QUOTA)}
            )
        assert report.minute_pass_outcome is MinutePassOutcome.QUOTA_EXHAUSTED
        assert report.minute_trailing_completed is False
        abort_lines = [
            r.getMessage()
            for r in caplog.records
            if "minute pass aborted" in r.getMessage()
        ]
        assert len(abort_lines) == 1
        assert str(MinutePassPhase.TRAILING) in abort_lines[0]
        assert str(MinutePassOutcome.QUOTA_EXHAUSTED) in abort_lines[0]
        assert "9 not attempted" in abort_lines[0]

    def test_a_402_in_the_backfill_phase_records_the_trailing_phase_completed(
        self,
    ) -> None:
        """The exit mapping needs this: a 402 after the trailing phase is the
        designed steady state, not a failure to collect the current session."""
        symbols = ["AAPL", "MSFT"]
        calls = {"n": 0}

        def _fake_process(symbol: str, **kwargs) -> MinuteSymbolResult:
            calls["n"] += 1
            # Fail only once the backfill phase has started (seed=False).
            if kwargs.get("seed") is False:
                return self._failure(MinuteFailureKind.PROVIDER_QUOTA)
            return MinuteSymbolResult(LastAttemptOutcome.SUCCESS, None, None, 1, 0)

        with ExitStack() as stack:

            def mp(target: str, **kw) -> MagicMock:
                return stack.enter_context(patch(target, **kw))

            mp(
                "manta_trading.data.acquisition.daemon.minute.Settings",
                return_value=_FakeSettings(),
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute._process_minute_symbol",
                side_effect=_fake_process,
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.build_minute_coverage_index",
                return_value={},
            )
            pool_cls = mp("manta_trading.data.acquisition.daemon.minute.ConnectionPool")
            http_cls = mp("manta_trading.data.acquisition.daemon.minute.httpx.Client")
            pool = MagicMock()
            pool_cls.return_value.__enter__ = MagicMock(return_value=pool)
            pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            conn = MagicMock()
            pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
            pool.connection.return_value.__exit__ = MagicMock(return_value=False)
            http = MagicMock()
            http_cls.return_value.__enter__ = MagicMock(return_value=http)
            http_cls.return_value.__exit__ = MagicMock(return_value=False)

            report = run_minute_cycle(symbols=symbols)

        assert report.minute_pass_outcome is MinutePassOutcome.QUOTA_EXHAUSTED
        assert report.minute_trailing_completed is True

    def test_a_completed_pass_reports_complete(self) -> None:
        report, attempted = self._run_pass(["AAPL", "MSFT"], {})
        assert report.minute_pass_outcome is MinutePassOutcome.COMPLETE
        assert report.minute_trailing_completed is True
        assert len(attempted) == 4


# ---------------------------------------------------------------------------
# Slice 921 Section 4 — the exit mapping (Task 4.7)
# ---------------------------------------------------------------------------


class TestMinutePassExitMapping:
    """The process exit code is one lookup, so an operator (and the systemd
    unit) sees the same number for the same condition every time."""

    def test_complete_exits_zero(self) -> None:
        assert (
            minute_pass_exit_code(MinutePassOutcome.COMPLETE, trailing_completed=True)
            == MINUTE_EXIT_OK
        )

    def test_quota_exhausted_inside_the_trailing_phase_exits_three(self) -> None:
        """The allowance ran out before every symbol's current session was
        attempted — the data the firing exists to collect is missing."""
        assert (
            minute_pass_exit_code(
                MinutePassOutcome.QUOTA_EXHAUSTED, trailing_completed=False
            )
            == MINUTE_EXIT_PASS_INCOMPLETE
        )

    def test_quota_exhausted_after_the_trailing_phase_exits_zero(self) -> None:
        """The designed steady state: the current session is collected and the
        rest of the allowance went to backfill. Alerting here would alert
        nightly."""
        assert (
            minute_pass_exit_code(
                MinutePassOutcome.QUOTA_EXHAUSTED, trailing_completed=True
            )
            == MINUTE_EXIT_OK
        )

    @pytest.mark.parametrize("trailing_completed", [True, False])
    def test_provider_unavailable_exits_three_in_either_phase(
        self, trailing_completed: bool
    ) -> None:
        assert (
            minute_pass_exit_code(
                MinutePassOutcome.PROVIDER_UNAVAILABLE,
                trailing_completed=trailing_completed,
            )
            == MINUTE_EXIT_PASS_INCOMPLETE
        )

    def test_every_outcome_has_a_code(self) -> None:
        """A new MinutePassOutcome member must not silently exit 0."""
        for outcome in MinutePassOutcome:
            for trailing in (True, False):
                assert isinstance(
                    minute_pass_exit_code(outcome, trailing_completed=trailing), int
                )


class TestRunnerCarriesTheExitCode:
    """The path from the cycle's report to sys.exit — none existed before."""

    @staticmethod
    def _runner_with(reports: list[object]):
        from manta_trading.data.acquisition.daemon.runner import Runner

        return Runner, iter(reports)

    def test_worst_seen_survives_a_later_clean_cycle(self) -> None:
        """The --forever multi-cycle rule. A process that missed a session and
        then had a clean cycle has still missed that session; exiting 0 would
        erase the only signal of it."""
        from manta_trading.data.acquisition.daemon.runner import Runner

        runner = Runner.__new__(Runner)
        runner._minute_exit_code = MINUTE_EXIT_OK

        bad = CycleReport()
        bad.minute_pass_outcome = MinutePassOutcome.PROVIDER_UNAVAILABLE
        bad.minute_trailing_completed = False
        good = CycleReport()
        good.minute_pass_outcome = MinutePassOutcome.COMPLETE
        good.minute_trailing_completed = True

        runner._record_minute_pass_outcome(bad)
        runner._record_minute_pass_outcome(good)
        assert runner._minute_exit_code == MINUTE_EXIT_PASS_INCOMPLETE

    def test_a_clean_pass_leaves_the_code_at_zero(self) -> None:
        from manta_trading.data.acquisition.daemon.runner import Runner

        runner = Runner.__new__(Runner)
        runner._minute_exit_code = MINUTE_EXIT_OK
        report = CycleReport()
        report.minute_pass_outcome = MinutePassOutcome.COMPLETE
        report.minute_trailing_completed = True
        runner._record_minute_pass_outcome(report)
        assert runner._minute_exit_code == MINUTE_EXIT_OK

    def test_a_daily_only_report_leaves_the_code_untouched(self) -> None:
        """CycleReport is shared; a daily report carries no minute outcome."""
        from manta_trading.data.acquisition.daemon.runner import Runner

        runner = Runner.__new__(Runner)
        runner._minute_exit_code = MINUTE_EXIT_OK
        runner._record_minute_pass_outcome(CycleReport())
        assert runner._minute_exit_code == MINUTE_EXIT_OK

    def test_a_post_trailing_quota_abort_does_not_raise_the_code(self) -> None:
        from manta_trading.data.acquisition.daemon.runner import Runner

        runner = Runner.__new__(Runner)
        runner._minute_exit_code = MINUTE_EXIT_OK
        report = CycleReport()
        report.minute_pass_outcome = MinutePassOutcome.QUOTA_EXHAUSTED
        report.minute_trailing_completed = True
        runner._record_minute_pass_outcome(report)
        assert runner._minute_exit_code == MINUTE_EXIT_OK


class TestChunkJudgedBySessions:
    """Slice 921 / #22: a chunk's gap row survives until its sessions hold bars.

    On 2026-09-10 the 01:21 UTC firing asked for the 09-09 session before
    EODHD had published it. The response held one bar — the 09-08 session's
    20:00 ET after-hours bar, which EODHD dates 00:00 UTC 09-09 — and
    ``classify_outcome``'s date comparison called that SUCCESS, so
    ``_advance_minute_gap`` deleted the row. The chunk is now judged per
    session, so that response is PARTIAL and the row is kept.
    """

    OPEN = datetime(2026, 9, 9, 13, 30, tzinfo=UTC)
    CLOSE = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)

    @staticmethod
    def _bar(ts: datetime) -> dict:
        return {
            "timestamp": int(ts.timestamp()),
            "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        }

    def _run(
        self,
        bars: list[dict],
        *,
        bounds: dict[datetime, datetime] | None = None,
        gap: tuple[datetime, datetime] | None = None,
    ) -> tuple[MagicMock, MagicMock, list[str]]:
        """One chunk over one session; returns (advance, logger, executed SQL)."""
        bounds = bounds or {self.OPEN: self.CLOSE}
        gap = gap or (self.OPEN, self.CLOSE)
        gap_iter = iter([_gap(*gap), None])
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone.return_value = (True, False, True, None)
        conn.cursor.return_value = cur
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
        response = MagicMock(status_code=200, json=MagicMock(return_value=bars))
        advance = MagicMock(return_value=None)
        logger = MagicMock()

        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.eodhd_get",
                return_value=response,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.fetch_session_bounds",
                return_value=bounds,
            ),
            patch("manta_trading.data.acquisition.daemon.minute.update_data_gaps"),
            patch(
                "manta_trading.data.acquisition.daemon.minute._advance_minute_gap",
                advance,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._record_minute_attempt",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                return_value=datetime(2004, 1, 1, tzinfo=UTC),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
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
            patch("manta_trading.data.acquisition.daemon.minute._logger", logger),
        ):
            _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                via=FetchEntryPoint.CYCLE,
                window=None,
                coverage_index=None,
            )
        executes = [str(c.args[0]) for c in cur.execute.call_args_list if c.args]
        return advance, logger, executes

    def test_spillover_only_for_a_recent_session_keeps_the_row(self) -> None:
        """Closed within the publication lag: not published yet — PARTIAL."""
        now = datetime.now(UTC)
        recent_close = now - timedelta(hours=1)
        recent_open = recent_close - timedelta(hours=6, minutes=30)
        spillover = self._bar(recent_open.replace(hour=0, minute=0))
        advance, logger, executes = self._run(
            [spillover],
            bounds={recent_open: recent_close},
            gap=(recent_open, recent_close),
        )
        assert advance.call_args.kwargs["outcome"] is LastAttemptOutcome.PARTIAL
        assert advance.call_args.kwargs["fetch_status"] is FetchStatus.UNKNOWN
        assert [c for c in logger.info.call_args_list if "not published" in c.args[0]]
        assert not [sql for sql in executes if "PROVIDER_HOLE" in sql]

    def test_spillover_only_for_an_old_session_records_a_hole(self) -> None:
        """Closed long ago (2026-09-09 vs a real now): the provider answered
        and had nothing — SUCCESS, and a PROVIDER_HOLE row so nothing asks
        again."""
        spillover = self._bar(datetime(2026, 9, 9, 0, 0, tzinfo=UTC))
        advance, logger, executes = self._run([spillover])
        assert advance.call_args.kwargs["outcome"] is LastAttemptOutcome.SUCCESS
        holes = [
            sql
            for sql in executes
            if "PROVIDER_HOLE" in sql or "INSERT INTO data_gaps" in sql
        ]
        assert holes, "the empty session must be persisted as PROVIDER_HOLE"
        assert [c for c in logger.info.call_args_list if "PROVIDER_HOLE" in c.args[0]]

    def test_full_session_deletes_the_row(self) -> None:
        bars = [self._bar(self.OPEN + timedelta(minutes=m)) for m in range(1, 391)]
        advance, logger, _ = self._run(bars)
        assert advance.call_args.kwargs["outcome"] is LastAttemptOutcome.SUCCESS
        assert advance.call_args.kwargs["fetch_status"] is None
        assert not [
            c for c in logger.info.call_args_list if "not published" in c.args[0]
        ]

    def test_last_bar_before_the_chunk_end_is_still_success(self) -> None:
        """The weekend tail: bars stop at Friday's close, chunk ends Sunday."""
        friday_close = datetime(2026, 9, 11, 20, 0, tzinfo=UTC)
        sunday = datetime(2026, 9, 13, tzinfo=UTC)
        gap_iter = iter([_gap(self.OPEN, sunday), None])
        friday_open = friday_close.replace(hour=13, minute=30)
        bounds = {self.OPEN: self.CLOSE, friday_open: friday_close}
        bars = [self._bar(self.OPEN + timedelta(minutes=1))] + [
            self._bar(friday_close.replace(hour=13, minute=31))
        ]
        advance = MagicMock(return_value=None)
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
        response = MagicMock(status_code=200, json=MagicMock(return_value=bars))
        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.eodhd_get",
                return_value=response,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.fetch_session_bounds",
                return_value=bounds,
            ),
            patch("manta_trading.data.acquisition.daemon.minute.update_data_gaps"),
            patch(
                "manta_trading.data.acquisition.daemon.minute._advance_minute_gap",
                advance,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._record_minute_attempt",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                return_value=datetime(2004, 1, 1, tzinfo=UTC),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
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
        ):
            _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                via=FetchEntryPoint.CYCLE,
                window=None,
                coverage_index=None,
            )
        # classify_outcome alone would say PARTIAL (last bar 09-11 < 09-13).
        assert advance.call_args.kwargs["outcome"] is LastAttemptOutcome.SUCCESS


class TestTruncatedDayAwareSeeding:
    """Slice 921 / #22: the trailing seed treats truncated days as uncovered.

    The coverage index reports a day as covered when it holds any bar, so the
    4,278 sessions the 2026-09-10 firing truncated to one spillover bar were
    never re-seeded. The trailing walk now passes the repair's truncated-day
    index as ``uncovered_days``; the backfill walk (which does not seed)
    receives nothing.
    """

    DAY = date(2026, 9, 9)

    def test_do_minute_symbol_forwards_uncovered_days_to_the_seed(self) -> None:
        conn = MagicMock()
        txn = MagicMock()
        txn.__enter__ = MagicMock(return_value=txn)
        txn.__exit__ = MagicMock(return_value=False)
        conn.transaction.return_value = txn
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        # (has_bars, has_unknown_gaps, has_any_gaps, gap_frontier): full seed.
        cur.fetchone.return_value = (True, True, True, None)
        conn.cursor.return_value = cur
        pool = MagicMock()
        pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
        pool.connection.return_value.__exit__ = MagicMock(return_value=False)
        lock_cm = MagicMock()
        lock_cm.__enter__ = MagicMock(return_value=None)
        lock_cm.__exit__ = MagicMock(return_value=False)
        compute = MagicMock(return_value=[])
        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.compute_missing_minute_sessions",
                compute,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.update_data_gaps",
                return_value=MagicMock(gaps_inserted=0),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start",
                return_value=datetime(2004, 1, 1, tzinfo=UTC),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.advisory_lock",
                return_value=lock_cm,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.pick_most_recent_actionable_gap",
                return_value=None,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
            ),
        ):
            _do_minute_symbol(
                "AAPL",
                pool=pool,
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                via=FetchEntryPoint.CYCLE,
                coverage_index={"AAPL": {self.DAY}},
                uncovered_days=frozenset({self.DAY}),
            )
        compute.assert_called_once()
        assert compute.call_args.kwargs["uncovered_days"] == {self.DAY}

    def test_run_minute_phase_hands_each_symbol_its_own_days(self) -> None:
        from manta_trading.data.acquisition.daemon.minute import _run_minute_phase

        process = MagicMock(
            return_value=MinuteSymbolResult(
                outcome=LastAttemptOutcome.SUCCESS,
                first_chunk_end=None,
                last_chunk_end=None,
                chunk_count=0,
                gaps_seeded=0,
                failure_kind=MinuteFailureKind.NONE,
            )
        )
        with patch(
            "manta_trading.data.acquisition.daemon.minute._process_minute_symbol",
            process,
        ):
            _run_minute_phase(
                ["AAPL", "MSFT"],
                phase=MinutePassPhase.TRAILING,
                pool=MagicMock(),
                http=MagicMock(),
                settings=cast("Settings", _FakeSettings()),
                coverage_index={},
                report=CycleReport(),
                should_continue=None,
                on_symbol=None,
                min_gap_end=None,
                max_chunks=1,
                seed=True,
                truncated_index={"AAPL": frozenset({self.DAY})},
            )
        by_symbol = {
            c.args[0]: c.kwargs["uncovered_days"] for c in process.call_args_list
        }
        assert by_symbol == {"AAPL": frozenset({self.DAY}), "MSFT": None}

    def test_scan_timeout_is_logged_and_the_cycle_seeds_without_it(self) -> None:
        from manta_trading.data.acquisition.daemon.minute import _build_truncated_index
        from manta_trading.data.gaps.repair_921 import RepairScanTimeout

        logger = MagicMock()
        with (
            patch(
                "manta_trading.data.acquisition.daemon.minute.build_truncated_day_index",
                side_effect=RepairScanTimeout("scan exceeded 600s"),
            ),
            patch("manta_trading.data.acquisition.daemon.minute._logger", logger),
        ):
            assert _build_truncated_index(MagicMock(), since=datetime.now(UTC)) is None
        logger.exception.assert_called_once()
        assert "truncated-day scan" in logger.exception.call_args.args[0]

    def test_only_the_trailing_phase_receives_the_index(self) -> None:
        phases: list[tuple[MinutePassPhase, object]] = []

        def _fake_phase(_symbols, *, phase, truncated_index=None, **_kw):
            phases.append((phase, truncated_index))
            return 1, MinutePassOutcome.COMPLETE

        with ExitStack() as stack:

            def mp(target: str, **kwargs) -> MagicMock:
                return stack.enter_context(patch(target, **kwargs))

            mp(
                "manta_trading.data.acquisition.daemon.minute.Settings",
                return_value=_FakeSettings(),
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.build_minute_coverage_index",
                return_value={},
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.build_truncated_day_index",
                return_value={"AAPL": frozenset({self.DAY})},
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute._run_minute_phase",
                side_effect=_fake_phase,
            )
            pool_cls = mp("manta_trading.data.acquisition.daemon.minute.ConnectionPool")
            http_cls = mp("manta_trading.data.acquisition.daemon.minute.httpx.Client")
            pool = MagicMock()
            pool_cls.return_value.__enter__ = MagicMock(return_value=pool)
            pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            http_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
            http_cls.return_value.__exit__ = MagicMock(return_value=False)
            run_minute_cycle(symbols=["AAPL"])

        assert [p for p, _ in phases] == [
            MinutePassPhase.TRAILING,
            MinutePassPhase.BACKFILL,
        ]
        assert phases[0][1] == {"AAPL": frozenset({self.DAY})}
        assert phases[1][1] is None


class TestTrailingCompletionLineIsASignal:
    """#22 review F001: the completion line stops the cutover's firing, so it
    is emitted only when the trailing phase actually completed."""

    @staticmethod
    def _cycle_with_trailing(outcome: MinutePassOutcome | None) -> MagicMock:
        logger = MagicMock()

        def _fake_phase(_symbols, *, phase, **_kw):
            if phase is MinutePassPhase.TRAILING:
                return 500, outcome
            return 0, MinutePassOutcome.COMPLETE

        with ExitStack() as stack:

            def mp(target: str, **kwargs) -> MagicMock:
                return stack.enter_context(patch(target, **kwargs))

            mp(
                "manta_trading.data.acquisition.daemon.minute.Settings",
                return_value=_FakeSettings(),
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.build_minute_coverage_index",
                return_value={},
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute.build_truncated_day_index",
                return_value={},
            )
            mp(
                "manta_trading.data.acquisition.daemon.minute._run_minute_phase",
                side_effect=_fake_phase,
            )
            mp("manta_trading.data.acquisition.daemon.minute._logger", new=logger)
            pool_cls = mp("manta_trading.data.acquisition.daemon.minute.ConnectionPool")
            http_cls = mp("manta_trading.data.acquisition.daemon.minute.httpx.Client")
            pool_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
            pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            http_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
            http_cls.return_value.__exit__ = MagicMock(return_value=False)
            run_minute_cycle(symbols=["AAPL"])
        return logger

    @staticmethod
    def _info_lines(logger: MagicMock) -> list[str]:
        return [
            c.args[0] % tuple(c.args[1:]) if len(c.args) > 1 else c.args[0]
            for c in logger.info.call_args_list
        ]

    def test_a_completed_phase_emits_the_constant(self) -> None:
        from manta_trading.constants import MINUTE_TRAILING_COMPLETE_LINE

        lines = self._info_lines(self._cycle_with_trailing(MinutePassOutcome.COMPLETE))
        assert MINUTE_TRAILING_COMPLETE_LINE.format(count=500) in lines

    def test_a_quota_aborted_phase_does_not(self) -> None:
        lines = self._info_lines(
            self._cycle_with_trailing(MinutePassOutcome.QUOTA_EXHAUSTED)
        )
        assert not [ln for ln in lines if ln.startswith("trailing phase complete")]
        assert any(ln.startswith("trailing phase ended early") for ln in lines)

    def test_a_shutdown_does_not(self) -> None:
        lines = self._info_lines(self._cycle_with_trailing(None))
        assert not [ln for ln in lines if ln.startswith("trailing phase complete")]


class TestACrashedPassIsIncomplete:
    """#22 review F004: a cycle that raised never reported, and the old path
    let the unit exit 0 for a pass that collected nothing."""

    def test_a_none_report_exits_pass_incomplete(self) -> None:
        from manta_trading.data.acquisition.daemon.runner import Runner

        runner = Runner.__new__(Runner)
        runner._minute_exit_code = MINUTE_EXIT_OK
        runner._record_minute_pass_outcome(None)
        assert runner._minute_exit_code == MINUTE_EXIT_PASS_INCOMPLETE

    def test_a_daily_only_report_still_leaves_the_code_alone(self) -> None:
        from manta_trading.data.acquisition.daemon.runner import Runner

        runner = Runner.__new__(Runner)
        runner._minute_exit_code = MINUTE_EXIT_OK
        runner._record_minute_pass_outcome(CycleReport())
        assert runner._minute_exit_code == MINUTE_EXIT_OK
