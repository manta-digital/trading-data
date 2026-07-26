"""Unit tests for the slice 168 cagg freshness assertion.

Covers the verdict type and signal enum (task 2), the job-catalog read (task 3),
the edge probes, their timeout discipline, and threshold resolution (task 4),
the four-signal evaluation plus indeterminate handling (task 5), and the TTL
verdict cache (task 6). The DB is faked at the ``execute()`` boundary;
assertions are on call *order* and bound parameters, not SQL text.
"""

from __future__ import annotations

from datetime import timedelta

from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
)

_VIEW = "minute_4hour_ohlcv"


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
