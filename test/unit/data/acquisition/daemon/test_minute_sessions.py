"""Session judgement of a minute chunk (slice 921, issue #22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from manta_trading.data.acquisition.daemon.minute_sessions import (
    bar_timestamp,
    judge_chunk_by_sessions,
    sessions_without_bars,
)
from manta_trading.data.acquisition.state import LastAttemptOutcome

# NYSE sessions 2026-09-08 and 2026-09-09, as trading_sessions stores them.
OPEN_08 = datetime(2026, 9, 8, 13, 30, tzinfo=UTC)
CLOSE_08 = datetime(2026, 9, 8, 20, 0, tzinfo=UTC)
OPEN_09 = datetime(2026, 9, 9, 13, 30, tzinfo=UTC)
CLOSE_09 = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)
BOUNDS = {OPEN_08: CLOSE_08, OPEN_09: CLOSE_09}


def _epoch_bar(ts: datetime) -> dict:
    """A bar in EODHD's real shape: epoch ``timestamp`` plus ISO ``datetime``."""
    return {
        "timestamp": int(ts.timestamp()),
        "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
        "volume": 1,
    }


def _session_bars(open_: datetime, close: datetime) -> list[dict]:
    """One bar per minute from the first minute after the open to the close."""
    bars = []
    ts = open_ + timedelta(minutes=1)
    while ts <= close:
        bars.append(_epoch_bar(ts))
        ts += timedelta(minutes=1)
    return bars


# The 20:00 ET after-hours bar of the 09-08 session, which EODHD dates 09-09.
SPILLOVER_09 = _epoch_bar(datetime(2026, 9, 9, 0, 0, tzinfo=UTC))


class TestBarTimestamp:
    def test_epoch_timestamp_wins(self) -> None:
        ts = datetime(2026, 9, 9, 13, 31, tzinfo=UTC)
        assert bar_timestamp(_epoch_bar(ts)) == ts

    def test_datetime_string_is_read_as_utc(self) -> None:
        assert bar_timestamp({"datetime": "2026-09-09 13:31:00"}) == datetime(
            2026, 9, 9, 13, 31, tzinfo=UTC
        )

    def test_unreadable_bar_is_none(self) -> None:
        assert bar_timestamp({"datetime": "not a time"}) is None
        assert bar_timestamp({}) is None


class TestSessionsWithoutBars:
    def test_spillover_only_bar_does_not_satisfy_the_session(self) -> None:
        """The 2026-09-10 cutover case: one 00:00 UTC bar dated day D."""
        assert sessions_without_bars([SPILLOVER_09], {OPEN_09: CLOSE_09}, CLOSE_09) == [
            OPEN_09
        ]

    def test_bar_at_the_open_does_not_satisfy_the_session(self) -> None:
        """Same predicate as the repair's truncated-day index: at-or-before open."""
        assert sessions_without_bars(
            [_epoch_bar(OPEN_09)], {OPEN_09: CLOSE_09}, CLOSE_09
        ) == [OPEN_09]

    def test_one_bar_inside_the_session_satisfies_it(self) -> None:
        bar = _epoch_bar(OPEN_09 + timedelta(minutes=1))
        assert sessions_without_bars([bar], {OPEN_09: CLOSE_09}, CLOSE_09) == []

    def test_bar_at_the_close_satisfies_it(self) -> None:
        assert (
            sessions_without_bars([_epoch_bar(CLOSE_09)], {OPEN_09: CLOSE_09}, CLOSE_09)
            == []
        )

    def test_previous_session_bars_do_not_satisfy_the_next(self) -> None:
        """The former four-day tolerance case: D-1 bars, no D bars."""
        bars = _session_bars(OPEN_08, CLOSE_08)
        assert sessions_without_bars(bars, BOUNDS, CLOSE_09) == [OPEN_09]

    def test_two_sessions_both_covered(self) -> None:
        bars = _session_bars(OPEN_08, CLOSE_08) + _session_bars(OPEN_09, CLOSE_09)
        assert sessions_without_bars(bars, BOUNDS, CLOSE_09) == []

    def test_session_the_chunk_cuts_through_is_not_judged(self) -> None:
        chunk_end = OPEN_09 + timedelta(hours=1)
        bars = _session_bars(OPEN_08, CLOSE_08)
        assert sessions_without_bars(bars, BOUNDS, chunk_end) == []

    def test_empty_bounds_judge_nothing(self) -> None:
        assert sessions_without_bars([SPILLOVER_09], {}, CLOSE_09) == []

    def test_datetime_string_bars_are_judged_too(self) -> None:
        bars = [{"datetime": "2026-09-09 13:31:00"}]
        assert sessions_without_bars(bars, {OPEN_09: CLOSE_09}, CLOSE_09) == []


class TestJudgeChunkBySessions:
    LATE = CLOSE_09 + timedelta(hours=1)  # within the publication lag
    LONG_AGO = CLOSE_09 + timedelta(days=2)  # well past it

    def test_spillover_only_within_the_lag_is_unpublished(self) -> None:
        j = judge_chunk_by_sessions(
            LastAttemptOutcome.SUCCESS,
            [SPILLOVER_09],
            {OPEN_09: CLOSE_09},
            CLOSE_09,
            now=self.LATE,
        )
        assert j.outcome is LastAttemptOutcome.PARTIAL
        assert j.unpublished == [OPEN_09] and j.empty == []

    def test_spillover_only_past_the_lag_is_an_empty_session(self) -> None:
        """AAA 2026-09-02: an illiquid name with no in-session trade. The
        provider answered; the session is a hole, not a pending fetch."""
        j = judge_chunk_by_sessions(
            LastAttemptOutcome.SUCCESS,
            [SPILLOVER_09],
            {OPEN_09: CLOSE_09},
            CLOSE_09,
            now=self.LONG_AGO,
        )
        assert j.outcome is LastAttemptOutcome.SUCCESS
        assert j.empty == [OPEN_09] and j.unpublished == []

    def test_previous_day_bars_within_the_lag_stay_partial(self) -> None:
        bars = _session_bars(OPEN_08, CLOSE_08)
        j = judge_chunk_by_sessions(
            LastAttemptOutcome.PARTIAL, bars, BOUNDS, CLOSE_09, now=self.LATE
        )
        assert j.outcome is LastAttemptOutcome.PARTIAL
        assert j.unpublished == [OPEN_09]

    def test_mixed_unpublished_and_empty_holds_the_row(self) -> None:
        """One session past the lag, one inside it: the row stays open for
        the unpublished one; the empty one is still reported for a hole."""
        open_10 = datetime(2026, 9, 10, 13, 30, tzinfo=UTC)
        close_10 = datetime(2026, 9, 10, 20, 0, tzinfo=UTC)
        bounds = {OPEN_09: CLOSE_09, open_10: close_10}
        j = judge_chunk_by_sessions(
            LastAttemptOutcome.PARTIAL,
            [SPILLOVER_09],
            bounds,
            close_10,
            now=close_10 + timedelta(hours=1),
        )
        assert j.outcome is LastAttemptOutcome.PARTIAL
        assert j.unpublished == [open_10] and j.empty == [OPEN_09]

    def test_all_sessions_covered_promotes_partial_to_success(self) -> None:
        """Replaces the weekend tolerance: last bar before a Sunday chunk end."""
        sunday = datetime(2026, 9, 13, tzinfo=UTC)
        bars = _session_bars(OPEN_08, CLOSE_08) + _session_bars(OPEN_09, CLOSE_09)
        j = judge_chunk_by_sessions(LastAttemptOutcome.PARTIAL, bars, BOUNDS, sunday)
        assert j.outcome is LastAttemptOutcome.SUCCESS
        assert j.unpublished == [] and j.empty == []

    def test_no_judgeable_session_keeps_the_classifier_verdict(self) -> None:
        bars = _session_bars(OPEN_08, CLOSE_08)
        for verdict in (LastAttemptOutcome.SUCCESS, LastAttemptOutcome.PARTIAL):
            j = judge_chunk_by_sessions(verdict, bars, {}, CLOSE_09)
            assert j.outcome is verdict and j.unpublished == [] and j.empty == []

    def test_outcomes_without_bars_pass_through(self) -> None:
        for verdict in (LastAttemptOutcome.EMPTY, LastAttemptOutcome.TRANSIENT_FAILURE):
            j = judge_chunk_by_sessions(verdict, [], {OPEN_09: CLOSE_09}, CLOSE_09)
            assert j.outcome is verdict and j.empty == []

    def test_success_with_an_empty_body_passes_through(self) -> None:
        j = judge_chunk_by_sessions(
            LastAttemptOutcome.SUCCESS, [], {OPEN_09: CLOSE_09}, CLOSE_09
        )
        assert j.outcome is LastAttemptOutcome.SUCCESS
