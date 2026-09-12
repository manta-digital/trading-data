"""Unit tests: the minute pass progress callback (slice 922, Task 2.3).

``on_progress`` is how a running pass tells ``pass_runs`` how far it has got,
so ``mt data overview`` can show progress rather than a bare "running". Three
properties matter:

1. It fires at the existing progress-log cadence, so it costs one extra call
   per 250 symbols and adds no new loop.
2. It fires once more when a phase ends, so a completed phase always reports
   ``done == total`` whatever the last interval landed on.
3. ``None`` leaves the pass byte-for-byte as it was — the whole existing
   minute suite runs with it unset.

The phase counters on ``CycleReport`` are checked here too: the close detail
reads them rather than re-deriving from ``symbol_outcomes``, which holds the
union of both phases and cannot say how far trailing got.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

from manta_trading.config import Settings
from manta_trading.constants import (
    MINUTE_SEED_PROGRESS_LOG_INTERVAL,
    MinutePassPhase,
)
from manta_trading.data.acquisition.daemon.daily import CycleReport
from manta_trading.data.acquisition.daemon.minute import _run_minute_phase
from manta_trading.data.acquisition.state import (
    LastAttemptOutcome,
    MinuteFailureKind,
)


class _FakeSettings:
    timescale_db_url = "postgresql://localhost/test"
    eodhd_api_key = "test-key"
    minute_history_start = None
    minute_firing_days = None


def _ok_result():
    from manta_trading.data.acquisition.daemon.minute import MinuteSymbolResult

    return MinuteSymbolResult(
        outcome=LastAttemptOutcome.SUCCESS,
        first_chunk_end=None,
        last_chunk_end=None,
        chunk_count=0,
        gaps_seeded=0,
        failure_kind=MinuteFailureKind.NONE,
    )


def _run(
    symbols: list[str],
    *,
    phase: MinutePassPhase = MinutePassPhase.TRAILING,
    report: CycleReport | None = None,
    on_progress=None,
) -> CycleReport:
    """Walk a phase over ``symbols`` with the per-symbol fetch stubbed out."""
    report = report if report is not None else CycleReport()
    with patch(
        "manta_trading.data.acquisition.daemon.minute._process_minute_symbol",
        MagicMock(return_value=_ok_result()),
    ):
        _run_minute_phase(
            symbols,
            phase=phase,
            pool=MagicMock(),
            http=MagicMock(),
            settings=cast("Settings", _FakeSettings()),
            coverage_index={},
            report=report,
            should_continue=None,
            on_symbol=None,
            min_gap_end=None,
            max_chunks=1,
            seed=False,
            on_progress=on_progress,
        )
    return report


class TestProgressCallback:
    def test_a_short_phase_reports_once_at_completion(self) -> None:
        calls: list[tuple[MinutePassPhase, int, int]] = []
        _run(["AAPL", "MSFT"], on_progress=lambda *a: calls.append(a))
        assert calls == [(MinutePassPhase.TRAILING, 2, 2)]

    def test_the_final_call_always_reports_done_equals_total(self) -> None:
        calls: list[tuple[MinutePassPhase, int, int]] = []
        symbols = [f"S{i}" for i in range(7)]
        _run(symbols, on_progress=lambda *a: calls.append(a))
        assert calls[-1] == (MinutePassPhase.TRAILING, 7, 7)

    def test_it_fires_at_the_existing_log_interval(self) -> None:
        """One call per interval, plus the completion call."""
        calls: list[tuple[MinutePassPhase, int, int]] = []
        n = MINUTE_SEED_PROGRESS_LOG_INTERVAL * 2
        symbols = [f"S{i}" for i in range(n)]
        _run(symbols, on_progress=lambda *a: calls.append(a))
        done_values = [c[1] for c in calls]
        assert done_values == [
            MINUTE_SEED_PROGRESS_LOG_INTERVAL,
            n,
            n,
        ]
        assert all(c[2] == n for c in calls)

    def test_the_phase_is_reported(self) -> None:
        calls: list[tuple[MinutePassPhase, int, int]] = []
        _run(
            ["AAPL"],
            phase=MinutePassPhase.BACKFILL,
            on_progress=lambda *a: calls.append(a),
        )
        assert calls[0][0] is MinutePassPhase.BACKFILL

    def test_none_is_accepted_and_changes_nothing(self) -> None:
        report = _run(["AAPL", "MSFT"], on_progress=None)
        assert report.success_count == 2


class TestPhaseCounters:
    def test_trailing_records_its_own_count(self) -> None:
        report = _run(["AAPL", "MSFT"], phase=MinutePassPhase.TRAILING)
        assert report.trailing_symbols_attempted == 2
        assert report.backfill_symbols_attempted == 0

    def test_backfill_records_its_own_count(self) -> None:
        report = _run(["AAPL"], phase=MinutePassPhase.BACKFILL)
        assert report.backfill_symbols_attempted == 1
        assert report.trailing_symbols_attempted == 0

    def test_both_phases_of_one_pass_are_kept_apart(self) -> None:
        """The detail line needs 'trailing n/N · backfill n', so one counter
        must not overwrite the other."""
        report = CycleReport()
        _run(["A", "B", "C"], phase=MinutePassPhase.TRAILING, report=report)
        _run(["A", "B"], phase=MinutePassPhase.BACKFILL, report=report)
        assert report.trailing_symbols_attempted == 3
        assert report.backfill_symbols_attempted == 2

    def test_a_fresh_report_starts_at_zero(self) -> None:
        report = CycleReport()
        assert report.trailing_symbols_attempted == 0
        assert report.backfill_symbols_attempted == 0
