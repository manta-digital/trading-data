"""Unit tests for run_daily_cycle and slice-148 extensions."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from manta_trading.data.acquisition.daemon.daily import (
    CycleReport,
    _do_daily_symbol,
    run_daily_cycle,
    run_daily_refetch,
)
from manta_trading.data.acquisition.outcomes import ProviderResponseError
from manta_trading.data.acquisition.quota import QuotaBucket
from manta_trading.data.acquisition.state import LastAttemptOutcome


@pytest.fixture(autouse=True)
def _quota_bucket_in_context():
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    bucket = QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)
    token = QUOTA_BUCKET_VAR.set(bucket)
    yield bucket
    QUOTA_BUCKET_VAR.reset(token)


UTC = timezone.utc


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, 0, tzinfo=UTC)


def _mock_response(status: int, bars: list | None = None, error_body: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    if error_body:
        resp.json.return_value = {"error": "no data"}
    elif bars is not None:
        resp.json.return_value = bars
    else:
        resp.json.return_value = []
    return resp


def _bar(date_str: str) -> dict:
    return {
        "date": date_str,
        "open": "100.0",
        "high": "101.0",
        "low": "99.0",
        "close": "100.5",
        "volume": "1000000",
    }


class _FakeSettings:
    timescale_db_url = "postgresql://localhost/test"
    eodhd_api_key = "test-key"
    market_db_url = None  # MarketDB removed in slice 152


class TestRunDailyCycleHappyPath:
    def _run(
        self,
        symbols: list[str],
        bars: list,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
    ) -> tuple[CycleReport, MagicMock]:
        mock_update_gaps = MagicMock(return_value=MagicMock(gaps_inserted=0, gaps_promoted_exhausted=0, terminal_rows_reset=0))

        with (
            patch("manta_trading.data.acquisition.daemon.daily.Settings", return_value=_FakeSettings()),
            patch("manta_trading.data.acquisition.daemon.daily.ConnectionPool") as mock_pool_cls,
            patch("manta_trading.data.acquisition.daemon.daily.httpx.Client") as mock_http_cls,
            patch("manta_trading.data.acquisition.daemon.daily._last_completed_session", return_value=_dt(2024, 12, 31)),
            patch("manta_trading.data.acquisition.daemon.daily.classify_outcome", return_value=outcome),
            patch("manta_trading.data.acquisition.daemon.daily.outcome_to_fetch_status", return_value=None),
            patch("manta_trading.data.acquisition.daemon.daily.update_data_gaps", mock_update_gaps),
            patch("manta_trading.data.acquisition.daemon.daily._insert_daily_bars"),
            patch("manta_trading.data.acquisition.daemon.daily._update_first_data_date"),
            patch("manta_trading.data.acquisition.daemon.daily._update_delisted_date_if_needed"),
            patch("manta_trading.data.acquisition.daemon.daily.advisory_lock"),
        ):
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

            mock_http = MagicMock()
            mock_http_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_http_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = _mock_response(200, bars)

            report = run_daily_cycle(symbols=symbols)

        return report, mock_update_gaps

    def test_success_count_for_full_range_bars(self) -> None:
        bars = [_bar("2024-01-02"), _bar("2024-12-31")]
        report, _ = self._run(["AAPL", "MSFT", "GOOGL"], bars)
        assert report.success_count == 3
        assert report.total == 3

    def test_update_data_gaps_called_once_per_symbol(self) -> None:
        bars = [_bar("2024-12-31")]
        _, mock_gaps = self._run(["AAPL", "MSFT", "GOOGL"], bars)
        assert mock_gaps.call_count == 3

    def test_update_gaps_called_with_success_outcome(self) -> None:
        bars = [_bar("2024-12-31")]
        _, mock_gaps = self._run(["AAPL"], bars)
        call_kwargs = mock_gaps.call_args.kwargs
        assert call_kwargs["outcome"] == LastAttemptOutcome.SUCCESS

    def test_cycle_report_has_symbol_outcomes(self) -> None:
        bars = [_bar("2024-12-31")]
        report, _ = self._run(["AAPL"], bars)
        assert "AAPL" in report.symbol_outcomes


class TestRunDailyCycleFailurePaths:
    def _run_single(self, resp_mock: MagicMock) -> CycleReport:
        mock_update_gaps = MagicMock(return_value=MagicMock(gaps_inserted=0, gaps_promoted_exhausted=0, terminal_rows_reset=0))

        with (
            patch("manta_trading.data.acquisition.daemon.daily.Settings", return_value=_FakeSettings()),
            patch("manta_trading.data.acquisition.daemon.daily.ConnectionPool") as mock_pool_cls,
            patch("manta_trading.data.acquisition.daemon.daily.httpx.Client") as mock_http_cls,
            patch("manta_trading.data.acquisition.daemon.daily._last_completed_session", return_value=_dt(2024, 12, 31)),
            patch("manta_trading.data.acquisition.daemon.daily.update_data_gaps", mock_update_gaps),
            patch("manta_trading.data.acquisition.daemon.daily._insert_daily_bars"),
            patch("manta_trading.data.acquisition.daemon.daily._update_first_data_date"),
            patch("manta_trading.data.acquisition.daemon.daily._update_delisted_date_if_needed"),
            patch("manta_trading.data.acquisition.daemon.daily.advisory_lock"),
        ):
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

            mock_http = MagicMock()
            mock_http_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_http_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = resp_mock

            return run_daily_cycle(symbols=["AAPL"])

    @pytest.mark.parametrize("status", [500, 503])
    def test_5xx_recorded_as_transient_failure(self, status: int) -> None:
        report = self._run_single(_mock_response(status))
        assert report.transient_failure_count == 1

    def test_429_recorded_as_transient_failure(self) -> None:
        report = self._run_single(_mock_response(429))
        assert report.transient_failure_count == 1

    def test_200_empty_recorded_as_empty(self) -> None:
        report = self._run_single(_mock_response(200, []))
        assert report.empty_count == 1

    def test_200_partial_recorded_as_partial(self) -> None:
        report = self._run_single(_mock_response(200, [_bar("2024-06-15")]))
        assert report.partial_count == 1

    def test_200_error_body_recorded_as_transient(self) -> None:
        report = self._run_single(_mock_response(200, error_body=True))
        assert report.transient_failure_count == 1

    def test_4xx_non_429_propagates(self) -> None:
        with pytest.raises(ProviderResponseError):
            self._run_single(_mock_response(404))


class TestDoDailySymbolExtensions:
    """Tests for the force_reset_terminal and window params added in slice 148."""

    def _run_do_daily(
        self,
        force_reset_terminal: bool = False,
        window: tuple[date, date] | None = None,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
    ) -> tuple[LastAttemptOutcome, MagicMock, MagicMock]:
        mock_update_gaps = MagicMock(return_value=MagicMock(gaps_inserted=0, gaps_promoted_exhausted=0, terminal_rows_reset=0))
        mock_coalesce = MagicMock(return_value=0)

        conn = MagicMock()
        txn = MagicMock()
        txn.__enter__ = MagicMock(return_value=txn)
        txn.__exit__ = MagicMock(return_value=False)
        conn.transaction.return_value = txn

        pool = MagicMock()
        pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
        pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        http = MagicMock()
        settings = _FakeSettings()

        with (
            patch("manta_trading.data.acquisition.daemon.daily._last_completed_session", return_value=_dt(2024, 12, 31)),
            patch("manta_trading.data.acquisition.daemon.daily.classify_outcome", return_value=outcome),
            patch("manta_trading.data.acquisition.daemon.daily.outcome_to_fetch_status", return_value=None),
            patch("manta_trading.data.acquisition.daemon.daily.update_data_gaps", mock_update_gaps),
            patch("manta_trading.data.acquisition.daemon.daily.coalesce_data_gaps", mock_coalesce),
            patch("manta_trading.data.acquisition.daemon.daily._insert_daily_bars"),
            patch("manta_trading.data.acquisition.daemon.daily._update_first_data_date"),
            patch("manta_trading.data.acquisition.daemon.daily._update_delisted_date_if_needed"),
            patch("manta_trading.data.acquisition.daemon.daily.advisory_lock"),
            patch("manta_trading.data.acquisition.daemon.daily.eodhd_get", return_value=_mock_response(200, [_bar("2024-12-31")])),
        ):
            result = _do_daily_symbol(
                "AAPL",
                pool=pool,
                http=http,
                settings=settings,
                via="cycle",
                force_reset_terminal=force_reset_terminal,
                window=window,
            )
        return result, mock_update_gaps, mock_coalesce

    def test_force_reset_terminal_true_forwarded_to_update_data_gaps(self) -> None:
        _, mock_update, _ = self._run_do_daily(force_reset_terminal=True)
        call_kwargs = mock_update.call_args.kwargs
        assert call_kwargs["force_reset_terminal"] is True

    def test_force_reset_terminal_false_default_forwarded(self) -> None:
        _, mock_update, _ = self._run_do_daily(force_reset_terminal=False)
        call_kwargs = mock_update.call_args.kwargs
        assert call_kwargs["force_reset_terminal"] is False

    def test_window_none_uses_epoch_start(self) -> None:
        _, mock_update, _ = self._run_do_daily(window=None)
        pos_args = mock_update.call_args.args
        from_ts = pos_args[3]
        assert from_ts.year == 1970

    def test_window_constrains_target_start(self) -> None:
        w = (date(2023, 11, 1), date(2023, 11, 30))
        _, mock_update, _ = self._run_do_daily(window=w)
        pos_args = mock_update.call_args.args
        from_ts = pos_args[3]
        assert from_ts.year == 2023
        assert from_ts.month == 11
        assert from_ts.day == 1

    def test_coalesce_not_called_inside_do_daily_symbol(self) -> None:
        _, _, mock_coalesce = self._run_do_daily()
        mock_coalesce.assert_not_called()

    def test_happy_path_logs_via_marker(self) -> None:
        """slice 165: mirrors the minute-side assertion — a successful daily
        fetch must emit at least one log line carrying via=."""
        with patch(
            "manta_trading.data.acquisition.daemon.daily._logger"
        ) as mock_logger:
            self._run_do_daily()
        via_calls = [
            call for call in mock_logger.info.call_args_list
            if "cycle" in call.args
        ]
        assert via_calls, "no INFO line carried via='cycle' on the happy path"


class TestViaMarkerThreading:
    """Tests for the via=refetch|cycle log marker added in slice 165."""

    def test_process_daily_symbol_forwards_via_to_do_daily_symbol(self) -> None:
        from manta_trading.data.acquisition.daemon.daily import (
            _process_daily_symbol,
        )

        mock_do = MagicMock(return_value=LastAttemptOutcome.SUCCESS)
        with patch(
            "manta_trading.data.acquisition.daemon.daily._do_daily_symbol",
            mock_do,
        ):
            _process_daily_symbol(
                "AAPL",
                pool=MagicMock(),
                http=MagicMock(),
                settings=_FakeSettings(),
                via="cycle",
            )
        assert mock_do.call_args.kwargs["via"] == "cycle"

    def test_process_daily_symbol_error_path_logs_via(self) -> None:
        from manta_trading.data.acquisition.daemon.daily import (
            _process_daily_symbol,
        )

        with (
            patch(
                "manta_trading.data.acquisition.daemon.daily._do_daily_symbol",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily._logger"
            ) as mock_logger,
        ):
            outcome = _process_daily_symbol(
                "AAPL",
                pool=MagicMock(),
                http=MagicMock(),
                settings=_FakeSettings(),
                via="refetch",
            )
        assert outcome == LastAttemptOutcome.TRANSIENT_FAILURE
        logged_args = mock_logger.exception.call_args.args
        assert "refetch" in logged_args


class TestRunDailyRefetch:
    def _run_refetch(
        self,
        symbol: str = "AAPL",
        from_date: date | None = None,
        to_date: date | None = None,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
    ) -> tuple[CycleReport, MagicMock, MagicMock]:
        mock_do_daily = MagicMock(return_value=outcome)
        mock_coalesce = MagicMock(return_value=0)
        mock_first_data = MagicMock(return_value=date(2010, 1, 1))
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
            patch("manta_trading.data.acquisition.daemon.daily.Settings", return_value=_FakeSettings()),
            patch("manta_trading.data.acquisition.daemon.daily._do_daily_symbol", mock_do_daily),
            patch("manta_trading.data.acquisition.daemon.daily.coalesce_data_gaps", mock_coalesce),
            patch("manta_trading.data.acquisition.daemon.daily._first_data_date", mock_first_data),
            patch("manta_trading.data.acquisition.daemon.daily._last_completed_session", mock_last_session),
            patch("manta_trading.data.acquisition.daemon.daily.httpx.Client"),
            patch("manta_trading.data.acquisition.daemon.daily.ConnectionPool") as mock_pool_cls,
        ):
            mock_pool_cls.return_value.__enter__ = MagicMock(return_value=mock_pool)
            mock_pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            report = run_daily_refetch(symbol, from_date=from_date, to_date=to_date)

        return report, mock_do_daily, mock_coalesce

    def test_from_date_none_resolves_to_first_data_date(self) -> None:
        _, mock_do, _ = self._run_refetch(from_date=None)
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["window"][0] == date(2010, 1, 1)

    def test_to_date_none_resolves_to_last_completed_session(self) -> None:
        _, mock_do, _ = self._run_refetch(to_date=None)
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["window"][1] == date(2024, 12, 31)

    def test_explicit_window_passed_through(self) -> None:
        fd = date(2023, 1, 1)
        td = date(2023, 12, 31)
        _, mock_do, _ = self._run_refetch(from_date=fd, to_date=td)
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["window"] == (fd, td)

    def test_force_reset_terminal_always_true(self) -> None:
        _, mock_do, _ = self._run_refetch()
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["force_reset_terminal"] is True

    def test_coalesce_called_after_do_daily_symbol(self) -> None:
        _, _, mock_coalesce = self._run_refetch()
        mock_coalesce.assert_called_once()
        args = mock_coalesce.call_args.args
        assert args[1] == "AAPL"
        assert args[2] == "daily"

    def test_success_outcome_increments_success_count(self) -> None:
        report, _, _ = self._run_refetch(outcome=LastAttemptOutcome.SUCCESS)
        assert report.success_count == 1
        assert report.total == 1

    def test_via_refetch_passed_to_do_daily_symbol(self) -> None:
        """Catches the missing-argument defect a mock-based _do_daily_symbol
        test alone cannot: asserts run_daily_refetch's real call site passes
        via="refetch", not just that _do_daily_symbol itself accepts it."""
        _, mock_do, _ = self._run_refetch()
        call_kwargs = mock_do.call_args.kwargs
        assert call_kwargs["via"] == "refetch"
