"""Unit tests for run_minute_cycle and slice-148 extensions (T26, T7, T9)."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from manta_trading.data.acquisition.daemon.minute import (
    _do_minute_symbol,
    run_minute_cycle,
    run_minute_refetch,
)
from manta_trading.data.acquisition.quota import QuotaBucket


@pytest.fixture(autouse=True)
def _quota_bucket_in_context():
    """Slice 146 requires a QuotaBucket on the contextvar for any
    eodhd_get call."""
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    bucket = QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)
    token = QUOTA_BUCKET_VAR.set(bucket)
    yield bucket
    QUOTA_BUCKET_VAR.reset(token)
from manta_trading.data.acquisition.outcomes import ProviderResponseError
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.gaps.actionable_gap_selector import GapRow

UTC = timezone.utc


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
    ) -> tuple:
        gap_iter = iter(gaps_sequence)
        mocks: dict[str, MagicMock] = {}

        with ExitStack() as stack:
            def mp(target: str, **kwargs) -> MagicMock:
                m = stack.enter_context(patch(target, **kwargs))
                mocks[target] = m
                return m

            mp("manta_trading.data.acquisition.daemon.minute.Settings",
               return_value=_FakeSettings())
            mp("manta_trading.data.acquisition.daemon.minute.classify_outcome",
               return_value=outcome)
            mp("manta_trading.data.acquisition.daemon.minute.outcome_to_fetch_status",
               return_value=None)

            pick_mock = mp(
                "manta_trading.data.acquisition.daemon.minute.pick_most_recent_actionable_gap",
                side_effect=lambda *a, **kw: next(gap_iter, None),
            )
            update_mock = mp(
                "manta_trading.data.acquisition.daemon.minute.update_data_gaps",
                return_value=MagicMock(gaps_inserted=0),
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
            mp("manta_trading.data.acquisition.daemon.minute._record_minute_attempt",
               return_value=None)
            coalesce_mock = mp(
                "manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps",
                return_value=0,
            )
            mp("manta_trading.data.acquisition.daemon.minute._insert_minute_bars")

            # advisory_lock as a passthrough context manager
            lock_cm = MagicMock()
            lock_cm.__enter__ = MagicMock(return_value=None)
            lock_cm.__exit__ = MagicMock(return_value=False)
            mp("manta_trading.data.acquisition.daemon.minute.advisory_lock",
               return_value=lock_cm)

            mock_pool_cls = mp("manta_trading.data.acquisition.daemon.minute.ConnectionPool")
            mock_http_cls = mp("manta_trading.data.acquisition.daemon.minute.httpx.Client")

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
                json=MagicMock(return_value=[{"timestamp": 1704196200, "open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000"}]),
            )

            report = run_minute_cycle(symbols=symbols)

        return report, pick_mock, update_mock, coalesce_mock, advance_mock

    def test_three_chunk_fetches_for_multi_month_gap(self) -> None:
        g1 = _gap(_dt(2024, 1, 1), _dt(2024, 4, 30))
        g2 = _gap(_dt(2024, 5, 1), _dt(2024, 8, 31))
        g3 = _gap(_dt(2024, 9, 1), _dt(2024, 12, 31))
        gaps = [g1, g2, g3, None]

        _, pick_mock, update_mock, coalesce_mock, advance_mock = self._run(["AAPL"], gaps)

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
        report, _, update_mock, coalesce_mock, _ = self._run(
            ["AAPL", "MSFT"], gaps
        )
        # Each symbol does 1 initial update_data_gaps call
        assert update_mock.call_count == 2
        # Each symbol does 1 coalesce
        assert coalesce_mock.call_count == 2


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
    ) -> tuple[LastAttemptOutcome, MagicMock, MagicMock]:
        """Call _do_minute_symbol with all external deps mocked. Returns (outcome, update_gaps_mock, coalesce_mock)."""
        if gaps_sequence is None:
            gaps_sequence = [None]  # no gaps → loop exits immediately

        gap_iter = iter(gaps_sequence)
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
        http.get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=[{"timestamp": 1704196200, "open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000"}]),
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
            patch("manta_trading.data.acquisition.daemon.minute.classify_outcome", return_value=outcome),
            patch("manta_trading.data.acquisition.daemon.minute.outcome_to_fetch_status", return_value=None),
            patch("manta_trading.data.acquisition.daemon.minute.update_data_gaps", mock_update_gaps),
            patch("manta_trading.data.acquisition.daemon.minute._advance_minute_gap", return_value=None),
            patch("manta_trading.data.acquisition.daemon.minute._record_minute_attempt", return_value=None),
            patch("manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start", return_value=resolved_start),
            patch("manta_trading.data.acquisition.daemon.minute.coalesce_data_gaps", mock_coalesce),
            patch("manta_trading.data.acquisition.daemon.minute._insert_minute_bars"),
            patch("manta_trading.data.acquisition.daemon.minute.advisory_lock", return_value=lock_cm),
            patch("manta_trading.data.acquisition.daemon.minute.eodhd_get", return_value=http.get.return_value),
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
                force_reset_terminal=force_reset_terminal,
                window=window,
            )
        return result, mock_update_gaps, mock_coalesce

    def test_force_reset_terminal_true_forwarded_to_initial_update_data_gaps(self) -> None:
        _, mock_update, _ = self._run_do_minute(force_reset_terminal=True)
        first_call_kwargs = mock_update.call_args_list[0].kwargs
        assert first_call_kwargs["force_reset_terminal"] is True

    def test_force_reset_terminal_false_default_forwarded(self) -> None:
        _, mock_update, _ = self._run_do_minute(force_reset_terminal=False)
        first_call_kwargs = mock_update.call_args_list[0].kwargs
        assert first_call_kwargs["force_reset_terminal"] is False

    def test_window_none_uses_resolved_history_start(self) -> None:
        """window=None → history_start = _resolve_minute_history_start()."""
        _, mock_update, _ = self._run_do_minute(window=None)
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
        _, mock_update, _ = self._run_do_minute(window=w)
        from_ts = mock_update.call_args_list[0].args[3]
        # window_start (2025-01-01) is well above the EODHD horizon (2004-01-01),
        # so history_start should equal window_start.
        assert from_ts.date() == future_start

    def test_coalesce_called_after_chunk_loop(self) -> None:
        """coalesce_data_gaps must be called after the chunk loop."""
        _, _, mock_coalesce = self._run_do_minute()
        mock_coalesce.assert_called_once()
        args = mock_coalesce.call_args.args
        assert args[1] == "AAPL"
        assert args[2] == "minute"

    def test_coalesce_called_on_refetch_path_too(self) -> None:
        """coalesce called even when force_reset_terminal=True."""
        _, _, mock_coalesce = self._run_do_minute(force_reset_terminal=True)
        mock_coalesce.assert_called_once()


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
    ) -> tuple:
        mock_do_minute = MagicMock(return_value=(outcome, None, None, 0))
        # The resolver returns 2010-01-01 (later than the EODHD horizon) so
        # tests can assert the per-symbol floor flows through.
        mock_resolve = MagicMock(
            return_value=datetime(2010, 1, 1, tzinfo=timezone.utc)
        )
        mock_last_session = MagicMock(return_value=_dt(2024, 12, 31))

        conn = MagicMock()
        txn = MagicMock()
        txn.__enter__ = MagicMock(return_value=txn)
        txn.__exit__ = MagicMock(return_value=False)
        conn.transaction.return_value = txn
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("manta_trading.data.acquisition.daemon.minute.Settings", return_value=_FakeSettings()),
            patch("manta_trading.data.acquisition.daemon.minute._do_minute_symbol", mock_do_minute),
            patch("manta_trading.data.acquisition.daemon.minute._resolve_minute_history_start", mock_resolve),
            patch("manta_trading.data.acquisition.daemon.minute._last_completed_session", mock_last_session),
            patch("manta_trading.data.acquisition.daemon.minute.httpx.Client"),
            patch("manta_trading.data.acquisition.daemon.minute.ConnectionPool") as mock_pool_cls,
        ):
            mock_pool_cls.return_value.__enter__ = MagicMock(return_value=mock_pool)
            mock_pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            report = run_minute_refetch(symbol, from_date=from_date, to_date=to_date)

        return report, mock_do_minute

    def test_from_date_none_uses_resolved_floor(self) -> None:
        """from_date=None → resolved_from = _resolve_minute_history_start()."""
        _, mock_do = self._run_refetch(from_date=None)
        call_kwargs = mock_do.call_args.kwargs
        window_from = call_kwargs["window"][0]
        # Resolver mock returns 2010-01-01.
        assert window_from == date(2010, 1, 1)

    def test_to_date_none_resolves_to_last_completed_session(self) -> None:
        _, mock_do = self._run_refetch(to_date=None)
        call_kwargs = mock_do.call_args.kwargs
        window_to = call_kwargs["window"][1]
        assert window_to == date(2024, 12, 31)

    def test_explicit_window_passed_through(self) -> None:
        fd = date(2024, 6, 1)
        td = date(2024, 9, 30)
        _, mock_do = self._run_refetch(from_date=fd, to_date=td)
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["window"] == (fd, td)

    def test_force_reset_terminal_always_true(self) -> None:
        _, mock_do = self._run_refetch()
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["force_reset_terminal"] is True

    def test_success_outcome_increments_success_count(self) -> None:
        report, _ = self._run_refetch(outcome=LastAttemptOutcome.SUCCESS)
        assert report.success_count == 1
        assert report.total == 1
