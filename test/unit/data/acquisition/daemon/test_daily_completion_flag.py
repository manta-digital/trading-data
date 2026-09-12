"""``daily_pass_completed`` means the walk reached the end (922 review F004).

The flag decides whether ``mt data overview`` reports the daily pass as
COMPLETE or INCOMPLETE, so a pass that stopped early must not claim to have
finished. It used to: the caller set the flag whenever
``_run_steady_state_cycle`` returned normally, and that helper returns
normally on a shutdown check before the provider call, on a bulk call that
failed, and after breaking mid-loop on shutdown.

These tests drive the real helper — the mode-dispatch tests mock it out, so
none of them could see this.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from manta_trading.data.acquisition.daemon.daily import (
    CycleReport,
    DailyWorkList,
    _run_steady_state_cycle,
    run_daily_cycle,
)
from manta_trading.data.acquisition.quota import QuotaBucket

_T0 = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)
_SYMBOLS = ["AAPL", "MSFT"]


@pytest.fixture(autouse=True)
def _quota_bucket_in_context():
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    bucket = QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)
    token = QUOTA_BUCKET_VAR.set(bucket)
    yield bucket
    QUOTA_BUCKET_VAR.reset(token)


def _settings() -> MagicMock:
    s = MagicMock()
    s.timescale_db_url = "postgresql://ts/db"
    s.eodhd_api_key = "testkey"
    return s


def _pool() -> MagicMock:
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value = conn
    return pool


def _run(*, should_continue=None, bars=None, get=None) -> CycleReport:
    """Drive the real helper with the provider and storage layers stubbed."""
    report = CycleReport()
    response = MagicMock()
    response.json.return_value = bars if bars is not None else []
    with (
        patch(
            "manta_trading.data.acquisition.daemon.daily.eodhd_get",
            side_effect=get,
            return_value=response,
        ),
        patch(
            "manta_trading.data.acquisition.daemon.daily._last_completed_session",
            return_value=_T0,
        ),
        patch("manta_trading.data.acquisition.daemon.daily._insert_daily_bars"),
        patch("manta_trading.data.acquisition.daemon.daily._update_first_data_date"),
        patch(
            "manta_trading.data.acquisition.daemon.daily"
            "._update_delisted_date_if_needed"
        ),
        patch("manta_trading.data.acquisition.daemon.daily.update_data_gaps"),
        patch("manta_trading.data.acquisition.daemon.daily.advisory_lock"),
    ):
        _run_steady_state_cycle(
            report=report,
            symbol_list=list(_SYMBOLS),
            pool=_pool(),
            http=MagicMock(),
            settings=_settings(),
            should_continue=should_continue,
            t0=_T0,
        )
    return report


class TestAFullWalkIsComplete:
    def test_every_symbol_attempted_sets_the_flag(self) -> None:
        report = _run()
        assert report.daily_pass_completed is True
        assert len(report.symbol_outcomes) == len(_SYMBOLS)


class TestTheCallerDoesNotOverrideIt:
    """Through ``run_daily_cycle``, which is where the bug actually lived.

    The caller used to set the flag whenever the helper returned normally,
    so a helper that stopped early was still recorded COMPLETE. Only a test
    that goes through the caller can see that.
    """

    def _cycle(self, *, helper) -> CycleReport:
        from manta_trading.constants import DailyMode

        settings = _settings()
        pool = MagicMock()
        pool.__enter__ = MagicMock(return_value=pool)
        pool.__exit__ = MagicMock(return_value=False)
        with (
            patch(
                "manta_trading.data.acquisition.daemon.daily.Settings",
                return_value=settings,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily.ConnectionPool",
                return_value=pool,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily.pending_daily_symbols",
                lambda _c, syms, _b: DailyWorkList(
                    pending=list(syms),
                    unactionable_no_calendar=[],
                    unknown_symbols=[],
                ),
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily._select_daily_mode",
                return_value=DailyMode.STEADY_STATE,
            ),
            patch(
                "manta_trading.data.acquisition.daemon.daily"
                "._run_steady_state_cycle",
                side_effect=helper,
            ),
        ):
            return run_daily_cycle(symbols=list(_SYMBOLS))

    def test_a_helper_that_stopped_early_stays_incomplete(self) -> None:
        """The helper returns without setting the flag; so must the caller."""

        def _stopped_early(*, report, **_kw):
            return report

        assert self._cycle(helper=_stopped_early).daily_pass_completed is False

    def test_a_helper_that_finished_reports_complete(self) -> None:
        def _finished(*, report, **_kw):
            report.daily_pass_completed = True
            return report

        assert self._cycle(helper=_finished).daily_pass_completed is True


class TestAnInterruptedWalkIsNot:
    """Each of these used to record COMPLETE for a pass that stopped early."""

    def test_shutdown_before_the_provider_call(self) -> None:
        report = _run(should_continue=lambda: False)
        assert report.daily_pass_completed is False
        assert report.symbol_outcomes == {}

    def test_a_failed_bulk_call(self) -> None:
        """No symbol was walked at all — every one is a transient failure."""
        report = _run(get=httpx.ReadTimeout("too slow"))
        assert report.daily_pass_completed is False
        assert report.transient_failure_count == len(_SYMBOLS)

    def test_shutdown_between_symbols(self) -> None:
        """The helper checks once at entry, then once per symbol: True, True
        walks the first symbol, and the third check stops before the second."""
        answers = iter([True, True, False])
        report = _run(should_continue=lambda: next(answers, False))
        assert report.daily_pass_completed is False
        assert len(report.symbol_outcomes) == 1
