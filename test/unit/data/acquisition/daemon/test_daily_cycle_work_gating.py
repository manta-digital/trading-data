"""Cycle-level tests for the slice 912 D1/D6 work gating in run_daily_cycle.

``test_pending_daily_symbols.py`` covers the derivation in isolation. These
tests cover what the cycle *does* with it: that an empty work list short-circuits
before any provider call, that a partial list narrows the scope handed downstream,
and that unactionable symbols are reported once rather than per symbol.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from manta_trading.constants import DailyMode
from manta_trading.data.acquisition.daemon.daily import (
    DailyWorkList,
    run_daily_cycle,
)
from manta_trading.data.acquisition.quota import QuotaBucket

_UTC = UTC
_MOD = "manta_trading.data.acquisition.daemon.daily"


@pytest.fixture(autouse=True)
def _quota_bucket_in_context():
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    bucket = QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)
    token = QUOTA_BUCKET_VAR.set(bucket)
    yield bucket
    QUOTA_BUCKET_VAR.reset(token)


class _FakeSettings:
    timescale_db_url = "postgresql://localhost/test"
    eodhd_api_key = "test-key"
    market_db_url = None


class _ExplodingHTTPClient:
    """Any attribute access is a test failure.

    A provider call on a drained scope is the specific waste this slice exists
    to prevent, so the assertion is "the client was never touched", not
    "get() was not called" — the latter would miss post/stream/request.
    """

    def __init__(self, *_args, **_kwargs):
        # Constructing the client is fine — the cycle opens it before deciding
        # whether it has work. Using it is what must not happen.
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def __getattr__(self, name):
        raise AssertionError(
            f"run_daily_cycle touched the HTTP client (.{name}) despite having "
            "no actionable work — this is the billable call D1 exists to avoid"
        )


def _pool_mock() -> MagicMock:
    pool = MagicMock()
    pool.__enter__ = MagicMock(return_value=pool)
    pool.__exit__ = MagicMock(return_value=False)
    conn_ctx = MagicMock()
    conn_ctx.__enter__ = MagicMock(return_value=MagicMock())
    conn_ctx.__exit__ = MagicMock(return_value=False)
    pool.connection.return_value = conn_ctx
    return pool


def _work(
    pending: list[str],
    unactionable: list[str] | None = None,
    unknown: list[str] | None = None,
):
    return lambda *_a, **_k: DailyWorkList(
        pending=list(pending),
        unactionable_no_calendar=list(unactionable or []),
        unknown_symbols=list(unknown or []),
    )


def test_drained_scope_makes_no_provider_call():
    """Every symbol already attempted this pass → return without touching HTTP."""
    with (
        patch(f"{_MOD}.Settings", return_value=_FakeSettings()),
        patch(f"{_MOD}.ConnectionPool", return_value=_pool_mock()),
        patch(f"{_MOD}.httpx.Client", _ExplodingHTTPClient),
        patch(f"{_MOD}.pending_daily_symbols", _work([])),
        patch(f"{_MOD}._select_daily_mode") as mode,
        patch(f"{_MOD}._run_steady_state_cycle") as steady,
        patch(f"{_MOD}._process_daily_symbol") as per_sym,
    ):
        report = run_daily_cycle(symbols=["AAPL", "MSFT"])

    assert report.nothing_actionable is True
    mode.assert_not_called()
    steady.assert_not_called()
    per_sym.assert_not_called()


def test_drained_scope_reports_zero_outcomes():
    """A no-op cycle must not fabricate outcome counts."""
    with (
        patch(f"{_MOD}.Settings", return_value=_FakeSettings()),
        patch(f"{_MOD}.ConnectionPool", return_value=_pool_mock()),
        patch(f"{_MOD}.httpx.Client", _ExplodingHTTPClient),
        patch(f"{_MOD}.pending_daily_symbols", _work([])),
    ):
        report = run_daily_cycle(symbols=["AAPL"])

    assert report.total == 0
    assert report.symbol_outcomes == {}


def test_partial_scope_narrows_to_pending_only():
    """A resumed pass hands only the unreached symbols downstream."""
    with (
        patch(f"{_MOD}.Settings", return_value=_FakeSettings()),
        patch(f"{_MOD}.ConnectionPool", return_value=_pool_mock()),
        patch(f"{_MOD}.httpx.Client"),
        patch(
            f"{_MOD}.pending_daily_symbols",
            _work(["DE", "EOG"]),
        ),
        patch(
            f"{_MOD}._select_daily_mode",
            return_value=DailyMode.STEADY_STATE,
        ) as mode,
        patch(
            f"{_MOD}._run_steady_state_cycle",
            return_value=MagicMock(
                success_count=2, partial_count=0, empty_count=0,
                transient_failure_count=0, wall_clock_seconds=0.1, symbol_outcomes={},
            ),
        ) as steady,
    ):
        run_daily_cycle(symbols=["AAPL", "BAC", "CAT", "DE", "EOG"])

    # Mode selection sees pending, not the full scope — a resumed pass should
    # not be pushed into BACKFILL by symbols it already finished.
    assert mode.call_args.args[1] == ["DE", "EOG"]
    assert steady.call_args.kwargs["symbol_list"] == ["DE", "EOG"]


def test_unactionable_symbols_warn_once_not_per_symbol(caplog):
    """906 per-symbol warnings is how this condition stayed invisible (D6)."""
    unactionable = [f"NOCAL{i}" for i in range(50)]
    with (
        patch(f"{_MOD}.Settings", return_value=_FakeSettings()),
        patch(f"{_MOD}.ConnectionPool", return_value=_pool_mock()),
        patch(f"{_MOD}.httpx.Client", _ExplodingHTTPClient),
        patch(
            f"{_MOD}.pending_daily_symbols",
            _work([], unactionable),
        ),
        caplog.at_level(logging.WARNING, logger=_MOD),
    ):
        report = run_daily_cycle(symbols=unactionable)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected one aggregate warning, got {len(warnings)}"
    assert "50" in warnings[0].getMessage()
    assert report.unactionable_no_calendar == 50


def test_all_unactionable_scope_terminates_without_provider_call():
    """The non-termination D6 prevents: these must not stay pending forever."""
    unactionable = ["NOCAL1", "NOCAL2"]
    with (
        patch(f"{_MOD}.Settings", return_value=_FakeSettings()),
        patch(f"{_MOD}.ConnectionPool", return_value=_pool_mock()),
        patch(f"{_MOD}.httpx.Client", _ExplodingHTTPClient),
        patch(
            f"{_MOD}.pending_daily_symbols",
            _work([], unactionable),
        ),
    ):
        report = run_daily_cycle(symbols=unactionable)

    assert report.nothing_actionable is True
    assert report.unactionable_no_calendar == 2


def test_pass_boundary_is_derived_from_cycle_start():
    """The boundary handed to the derivation is today's midnight + offset."""
    captured = {}

    def _capture(_conn, symbol_list, boundary):
        captured["boundary"] = boundary
        return DailyWorkList(
            pending=[], unactionable_no_calendar=[], unknown_symbols=[]
        )

    with (
        patch(f"{_MOD}.Settings", return_value=_FakeSettings()),
        patch(f"{_MOD}.ConnectionPool", return_value=_pool_mock()),
        patch(f"{_MOD}.httpx.Client", _ExplodingHTTPClient),
        patch(f"{_MOD}.pending_daily_symbols", _capture),
    ):
        run_daily_cycle(symbols=["AAPL"])

    boundary = captured["boundary"]
    today = datetime.now(UTC).date()
    assert boundary.tzinfo is not None
    assert boundary.astimezone(_UTC).date() == today
    assert (boundary.hour, boundary.minute) == (0, 30)
