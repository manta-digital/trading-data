"""Unit tests for daemon bulk-EOD steady-state path (slice 154 T12)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from manta_trading.constants import DailyMode
from manta_trading.data.acquisition.daemon.daily import (
    DailyWorkList,
    _select_daily_mode,
    _run_steady_state_cycle,
    run_daily_cycle,
)
from manta_trading.data.acquisition.quota import QuotaBucket
from manta_trading.data.acquisition.state import LastAttemptOutcome

_UTC = timezone.utc


def _all_pending(_conn, symbol_list, _boundary) -> DailyWorkList:
    """Stand-in for the slice 912 work-list derivation: everything is pending.

    These tests assert mode dispatch (bulk vs per-symbol), not which symbols
    are outstanding. Derivation is covered in ``test_pending_daily_symbols.py``.
    """
    return DailyWorkList(
        pending=list(symbol_list), unactionable_no_calendar=[], unknown_symbols=[]
    )


@pytest.fixture(autouse=True)
def _quota_bucket_in_context():
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    bucket = QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)
    token = QUOTA_BUCKET_VAR.set(bucket)
    yield bucket
    QUOTA_BUCKET_VAR.reset(token)


# ---------------------------------------------------------------------------
# _select_daily_mode
# ---------------------------------------------------------------------------


def _make_conn(unknown_gap_count: int, any_cold: bool) -> MagicMock:
    """Return a mock connection for _select_daily_mode.

    First fetchone → unknown gap count.
    Second fetchone → the anti-join EXISTS result: True when some pending
    symbol has no warm acquisition_state row. The real rendered query runs
    against a real database in ``test_daily_mode_selection_sql.py``.
    """
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.side_effect = [(unknown_gap_count,), (any_cold,)]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


class TestSelectDailyMode:
    def test_all_caught_up_with_bars_returns_steady_state(self):
        # 0 UNKNOWN gaps, every symbol has a warm attempt → STEADY_STATE
        conn = _make_conn(unknown_gap_count=0, any_cold=False)
        mode = _select_daily_mode(conn, ["AAPL", "MSFT"])
        assert mode == DailyMode.STEADY_STATE

    def test_unknown_gaps_returns_backfill(self):
        conn = _make_conn(unknown_gap_count=3, any_cold=False)
        mode = _select_daily_mode(conn, ["AAPL", "MSFT"])
        assert mode == DailyMode.BACKFILL

    def test_cold_symbol_no_bars_returns_backfill(self):
        # 0 UNKNOWN gaps but some symbol has no warm attempt → BACKFILL
        conn = _make_conn(unknown_gap_count=0, any_cold=True)
        mode = _select_daily_mode(conn, ["AAPL", "MSFT"])
        assert mode == DailyMode.BACKFILL

    def test_empty_symbol_list_returns_steady_state(self):
        conn = _make_conn(unknown_gap_count=0, any_cold=True)
        mode = _select_daily_mode(conn, [])
        assert mode == DailyMode.STEADY_STATE


# ---------------------------------------------------------------------------
# run_daily_cycle mode dispatch
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    timescale_url: str = "postgresql://ts/db",
    api_key: str = "testkey",
) -> MagicMock:
    s = MagicMock()
    s.timescale_db_url = timescale_url
    s.eodhd_api_key = api_key
    return s


class TestRunDailyCycleModeDispatch:
    """Verify that run_daily_cycle dispatches to the right sub-function."""

    def test_caught_up_scope_calls_steady_state_not_per_symbol(self):
        """When all symbols are caught up, bulk endpoint is called once and
        per-symbol /eod is NOT called."""
        settings = _make_settings()

        pool_mock = MagicMock()
        pool_mock.__enter__ = MagicMock(return_value=pool_mock)
        pool_mock.__exit__ = MagicMock(return_value=False)

        with (
            patch("manta_trading.data.acquisition.daemon.daily.Settings", return_value=settings),
            patch("manta_trading.data.acquisition.daemon.daily.ConnectionPool", return_value=pool_mock),
            patch(
                "manta_trading.data.acquisition.daemon.daily.pending_daily_symbols",
                _all_pending,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily._select_daily_mode",
                return_value=DailyMode.STEADY_STATE,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily._run_steady_state_cycle",
                return_value=MagicMock(
                    success_count=2,
                    partial_count=0,
                    empty_count=0,
                    transient_failure_count=0,
                    wall_clock_seconds=0.1,
                    symbol_outcomes={},
                ),
            ) as mock_steady,
            patch(
                "manta_trading.data.acquisition.daemon.daily._process_daily_symbol"
            ) as mock_per_sym,
        ):
            run_daily_cycle(symbols=["AAPL", "MSFT"])

        mock_steady.assert_called_once()
        mock_per_sym.assert_not_called()

    def test_backfill_scope_calls_per_symbol_not_bulk(self):
        """When any symbol has UNKNOWN gaps, per-symbol /eod is used."""
        settings = _make_settings()

        pool_mock = MagicMock()
        pool_mock.__enter__ = MagicMock(return_value=pool_mock)
        pool_mock.__exit__ = MagicMock(return_value=False)
        # Connection context manager for _select_daily_mode
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(return_value=MagicMock())
        conn_ctx.__exit__ = MagicMock(return_value=False)
        pool_mock.connection.return_value = conn_ctx

        with (
            patch("manta_trading.data.acquisition.daemon.daily.Settings", return_value=settings),
            patch("manta_trading.data.acquisition.daemon.daily.ConnectionPool", return_value=pool_mock),
            patch(
                "manta_trading.data.acquisition.daemon.daily.pending_daily_symbols",
                _all_pending,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily._select_daily_mode",
                return_value=DailyMode.BACKFILL,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily._run_steady_state_cycle"
            ) as mock_steady,
            patch(
                "manta_trading.data.acquisition.daemon.daily._process_daily_symbol",
                return_value=LastAttemptOutcome.SUCCESS,
            ) as mock_per_sym,
        ):
            run_daily_cycle(symbols=["AAPL"])

        mock_steady.assert_not_called()
        mock_per_sym.assert_called_once()

    def test_straggler_symbol_gets_per_symbol_call(self):
        """One symbol not caught up → bulk path used for caught-up ones,
        per-symbol path dispatched for the straggler.

        Note: the current implementation dispatches via _select_daily_mode
        at the cycle level (BACKFILL or STEADY_STATE for the whole scope).
        This test verifies that when mode=BACKFILL, per-symbol is called
        for all symbols (including the straggler)."""
        settings = _make_settings()

        pool_mock = MagicMock()
        pool_mock.__enter__ = MagicMock(return_value=pool_mock)
        pool_mock.__exit__ = MagicMock(return_value=False)
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(return_value=MagicMock())
        conn_ctx.__exit__ = MagicMock(return_value=False)
        pool_mock.connection.return_value = conn_ctx

        with (
            patch("manta_trading.data.acquisition.daemon.daily.Settings", return_value=settings),
            patch("manta_trading.data.acquisition.daemon.daily.ConnectionPool", return_value=pool_mock),
            patch(
                "manta_trading.data.acquisition.daemon.daily.pending_daily_symbols",
                _all_pending,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily._select_daily_mode",
                return_value=DailyMode.BACKFILL,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily._process_daily_symbol",
                return_value=LastAttemptOutcome.SUCCESS,
            ) as mock_per_sym,
        ):
            run_daily_cycle(symbols=["AAPL", "MSFT"])

        assert mock_per_sym.call_count == 2
