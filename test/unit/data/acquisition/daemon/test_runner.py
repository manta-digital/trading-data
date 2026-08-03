"""Unit tests for slice 146 runner predicates and main loop (T18)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from manta_trading.constants import (
    DAILY_CYCLE_RETRY_INTERVAL,
    DAILY_CYCLE_START_OFFSET,
)
from manta_trading.data.acquisition.daemon.daily import CycleReport
from manta_trading.data.acquisition.daemon.runner import (
    QUOTA_BUCKET_VAR,
    Runner,
    RunnerConfig,
    RunnerState,
    SCOPE_ALL_ACTIVE,
    ca_update_due,
    daily_cycle_due,
    minute_cycle_due,
    sleep_until_next_due_event,
)
from manta_trading.data.acquisition.quota import QuotaBucket

UTC = timezone.utc


def _today_at(hour: int, minute: int = 0) -> datetime:
    today = datetime.now(UTC).date()
    return datetime(today.year, today.month, today.day, hour, minute, tzinfo=UTC)


def _bucket() -> QuotaBucket:
    return QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)


# ---------------------------------------------------------------------------
# daily_cycle_due
# ---------------------------------------------------------------------------


def test_daily_cycle_due_false_before_start_offset():
    state = RunnerState(last_daily_cycle_end_utc=None)
    # Right at midnight UTC, before the provider has published late bars.
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    assert daily_cycle_due(state, midnight) is False


def test_daily_cycle_due_true_after_start_offset_with_no_history():
    state = RunnerState(last_daily_cycle_end_utc=None)
    after_offset = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + DAILY_CYCLE_START_OFFSET + timedelta(seconds=1)
    assert daily_cycle_due(state, after_offset) is True


def test_daily_cycle_due_false_within_retry_interval():
    """Cadence guard: a cycle that just ended does not immediately re-run.

    Replaces the old once-per-UTC-day gate (912 D2). The question is no longer
    "has today's pass happened" — an interrupted pass has happened and still
    has work — but "has enough time passed to try again".
    """
    end = datetime.now(UTC).replace(hour=2, minute=0, second=0, microsecond=0)
    state = RunnerState(last_daily_cycle_end_utc=end)
    assert daily_cycle_due(state, end + DAILY_CYCLE_RETRY_INTERVAL / 2) is False


def test_daily_cycle_due_true_after_retry_interval_same_day():
    """The defect the slice exists to fix: same-day retry after an interruption.

    Under the old predicate this returned False for the rest of the UTC day, so
    a pass that died partway through the alphabet was never retried.
    """
    end = datetime.now(UTC).replace(hour=2, minute=0, second=0, microsecond=0)
    state = RunnerState(last_daily_cycle_end_utc=end)
    later = end + DAILY_CYCLE_RETRY_INTERVAL + timedelta(seconds=1)
    assert daily_cycle_due(state, later) is True


def test_daily_cycle_due_true_after_utc_day_rollover():
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    state = RunnerState(last_daily_cycle_end_utc=today - timedelta(days=1))
    assert daily_cycle_due(state, today) is True


def test_daily_cycle_due_false_before_offset_even_when_retry_elapsed():
    """The start offset outranks the retry interval.

    Just after midnight, a cycle that ended hours ago has long since satisfied
    the retry interval — but the provider has not published the completed
    session yet, so the pass must still wait.
    """
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    state = RunnerState(last_daily_cycle_end_utc=midnight - timedelta(hours=6))
    assert daily_cycle_due(state, midnight + timedelta(minutes=5)) is False


# ---------------------------------------------------------------------------
# minute_cycle_due
# ---------------------------------------------------------------------------


def test_minute_cycle_due_true_when_no_history():
    state = RunnerState(last_minute_cycle_end_utc=None)
    assert minute_cycle_due(state, datetime.now(UTC)) is True


def test_minute_cycle_due_false_within_one_minute():
    now = datetime.now(UTC)
    state = RunnerState(last_minute_cycle_end_utc=now - timedelta(seconds=30))
    assert minute_cycle_due(state, now) is False


def test_minute_cycle_due_true_after_one_minute():
    now = datetime.now(UTC)
    state = RunnerState(last_minute_cycle_end_utc=now - timedelta(seconds=61))
    assert minute_cycle_due(state, now) is True


# ---------------------------------------------------------------------------
# ca_update_due
# ---------------------------------------------------------------------------


def _conn_returning_row(row: tuple | None) -> MagicMock:
    cur = MagicMock()
    cur.fetchone.return_value = row
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def test_ca_update_due_false_before_grace():
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    conn = _conn_returning_row(None)
    assert ca_update_due(conn, midnight) is False


def test_ca_update_due_true_when_row_missing():
    after_grace = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + DAILY_CYCLE_START_OFFSET + timedelta(seconds=1)
    conn = _conn_returning_row(None)
    assert ca_update_due(conn, after_grace) is True


def test_ca_update_due_true_when_last_attempt_ts_is_null():
    after_grace = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + DAILY_CYCLE_START_OFFSET + timedelta(seconds=1)
    conn = _conn_returning_row((None,))
    # MUST NOT call .date() on None.
    assert ca_update_due(conn, after_grace) is True


def test_ca_update_due_false_when_last_attempt_today():
    today_after_grace = datetime.now(UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    conn = _conn_returning_row((today_after_grace - timedelta(hours=1),))
    assert ca_update_due(conn, today_after_grace) is False


def test_ca_update_due_true_when_last_attempt_was_yesterday():
    today_after_grace = datetime.now(UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    yesterday = today_after_grace - timedelta(days=1)
    conn = _conn_returning_row((yesterday,))
    assert ca_update_due(conn, today_after_grace) is True


# ---------------------------------------------------------------------------
# sleep_until_next_due_event
# ---------------------------------------------------------------------------


def test_sleep_caps_at_60s():
    sleeps: list[float] = []
    state = RunnerState()
    sleep_until_next_due_event(
        state, datetime.now(UTC), sleeps.append, cap_seconds=60.0
    )
    assert sleeps and 0.0 <= sleeps[0] <= 60.0


def test_sleep_targets_retry_interval_not_tomorrow():
    """After a cycle ends, the next wake is one retry interval away (912 D2).

    Sleeping to tomorrow's start offset would strand an interrupted pass for the
    rest of the day — the defect this slice removes. The cap makes the observed
    sleep 60 s, so the assertion is that we did *not* sleep zero and did not
    treat the day as finished.
    """
    now = _today_at(4, 0)
    state = RunnerState(last_daily_cycle_end_utc=now - timedelta(minutes=1))
    sleeps: list[float] = []
    sleep_until_next_due_event(state, now, sleeps.append, cap_seconds=60.0)
    # Next daily wake is 14 minutes out, so the capped sleep is the full 60 s —
    # not the ~20 hours a next-day target would have produced (also capped, so
    # the distinguishing check is the uncapped math below).
    assert sleeps[0] == 60.0

    # Uncapped, the wait must be the retry interval remainder, not ~20 hours.
    sleeps.clear()
    sleep_until_next_due_event(state, now, sleeps.append, cap_seconds=86_400.0)
    expected = (DAILY_CYCLE_RETRY_INTERVAL - timedelta(minutes=1)).total_seconds()
    assert sleeps[0] == pytest.approx(expected, abs=1.0)


def test_sleep_does_not_crash_when_everything_is_already_due():
    """An empty set of future events means wait zero, never cap_seconds.

    Under the old code the daily candidate was always tomorrow's offset and so
    always positive; under D2 it can be `now`, which previously would have left
    `min()` with nothing to consume.
    """
    now = _today_at(12, 0)
    state = RunnerState(last_daily_cycle_end_utc=None)
    sleeps: list[float] = []
    sleep_until_next_due_event(state, now, sleeps.append, cap_seconds=60.0)
    assert sleeps == [0.0]


# ---------------------------------------------------------------------------
# Runner main loop
# ---------------------------------------------------------------------------


class _FrozenClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t


def _make_runner(
    *,
    scope=SCOPE_ALL_ACTIVE,
    terminate_when_drained: bool = True,
    granularities: frozenset[str] | None = None,
    daily_func: MagicMock | None = None,
    minute_func: MagicMock | None = None,
    ca_func: MagicMock | None = None,
    clock_at: datetime | None = None,
) -> tuple[Runner, MagicMock, MagicMock, MagicMock]:
    bucket = _bucket()
    # A real CycleReport, not a bare MagicMock: the loop reads
    # `report.nothing_actionable` straight off the contract, and a mock would
    # auto-create it as a truthy attribute — silently reporting every scope as
    # drained (912 review F003).
    daily_func = daily_func or MagicMock(return_value=CycleReport())
    minute_func = minute_func or MagicMock(return_value=CycleReport())
    ca_func = ca_func or MagicMock()
    # Simulate ca_update_due returning False so the loop doesn't try
    # to call ca_func; tests opt in by reaching past the grace period.
    conn = _conn_returning_row(
        (datetime.now(UTC),)  # "ca update was today" → not due
    )
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    conn_factory = MagicMock(return_value=cm)
    config = RunnerConfig(
        scope=scope,
        granularities=granularities or frozenset({"daily", "minute"}),
        terminate_when_drained=terminate_when_drained,
    )
    runner = Runner(
        config=config,
        bucket=bucket,
        conn_factory=conn_factory,
        run_daily_cycle=daily_func,
        run_minute_cycle=minute_func,
        run_ca_update=ca_func,
        clock=(clock_at and _FrozenClock(clock_at).now) or None,
    )
    return runner, daily_func, minute_func, ca_func


def test_runner_terminates_when_drained_after_one_pass():
    # Use a clock past midnight + grace so daily is due and runs once.
    after_grace = datetime.now(UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    runner, daily_func, minute_func, _ = _make_runner(
        clock_at=after_grace,
        granularities=frozenset({"daily"}),
    )
    code = runner.start()
    assert code == 0
    assert daily_func.call_count == 1
    assert minute_func.call_count == 0


def test_runner_stamps_cycle_end_not_start():
    """The stamp lands after the cycle, and reflects completion time (912 D2).

    Under the old code the stamp was written *before* the try block, so a cycle
    that died partway had already marked the day done.
    """
    at = _today_at(12, 0)
    observed: list[datetime | None] = []

    def _record_state_during_cycle(**_kwargs):
        # Mid-cycle the stamp must still be unset: nothing has completed.
        observed.append(runner._state.last_daily_cycle_end_utc)
        return CycleReport()

    runner, _daily, _minute, _ca = _make_runner(
        clock_at=at,
        granularities=frozenset({"daily"}),
        daily_func=MagicMock(side_effect=_record_state_during_cycle),
    )
    runner.start()

    assert observed == [None], "stamp was written before the cycle ran"
    assert runner._state.last_daily_cycle_end_utc == at


def test_runner_stamps_cycle_end_even_when_cycle_raises():
    """A cycle that raised still consumed its cadence slot.

    Not stamping would busy-loop against a persistent failure. No work is lost:
    remaining symbols are re-derived from acquisition_state on the next tick.
    """
    at = _today_at(12, 0)
    runner, _daily, _minute, _ca = _make_runner(
        clock_at=at,
        granularities=frozenset({"daily"}),
        daily_func=MagicMock(side_effect=RuntimeError("cycle blew up")),
    )
    code = runner.start()

    assert code == 0, "a raising cycle must not crash the daemon"
    assert runner._state.last_daily_cycle_end_utc == at


def test_runner_max_credits_exhausted_exits():
    bucket = QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)
    # Pretend we already spent 100 credits.
    bucket._spent_log = [(0.0, 100)]  # type: ignore[attr-defined]
    config = RunnerConfig(
        scope=SCOPE_ALL_ACTIVE,
        granularities=frozenset({"daily"}),
        max_credits=50,  # already exceeded
    )
    daily_func = MagicMock()
    conn = _conn_returning_row((datetime.now(UTC),))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    runner = Runner(
        config=config, bucket=bucket,
        conn_factory=MagicMock(return_value=cm),
        run_daily_cycle=daily_func,
        run_minute_cycle=MagicMock(),
    )
    code = runner.start()
    assert code == 0
    daily_func.assert_not_called()


def test_runner_sigterm_flag_breaks_loop():
    bucket = _bucket()
    daily_func = MagicMock()

    def force_exit_after_first_call(*args, **kwargs):
        # As the very first cycle starts, simulate SIGTERM arriving.
        runner._should_exit = True

    daily_func.side_effect = force_exit_after_first_call
    after_grace = datetime.now(UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    config = RunnerConfig(
        scope=SCOPE_ALL_ACTIVE,
        granularities=frozenset({"daily"}),
        terminate_when_drained=False,  # would loop forever without SIGTERM
    )
    conn = _conn_returning_row((datetime.now(UTC),))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    runner = Runner(
        config=config, bucket=bucket,
        conn_factory=MagicMock(return_value=cm),
        run_daily_cycle=daily_func,
        run_minute_cycle=MagicMock(),
        clock=_FrozenClock(after_grace).now,
    )
    code = runner.start()
    assert code == 0
    assert daily_func.call_count == 1


def test_runner_should_continue_passes_through_to_cycle():
    bucket = _bucket()
    captured: dict[str, object] = {}

    def daily_with_capture(*args, **kwargs):
        captured["should_continue"] = kwargs.get("should_continue")

    daily_func = MagicMock(side_effect=daily_with_capture)
    after_grace = datetime.now(UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    config = RunnerConfig(
        scope=SCOPE_ALL_ACTIVE,
        granularities=frozenset({"daily"}),
        terminate_when_drained=True,
    )
    conn = _conn_returning_row((datetime.now(UTC),))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    runner = Runner(
        config=config, bucket=bucket,
        conn_factory=MagicMock(return_value=cm),
        run_daily_cycle=daily_func,
        run_minute_cycle=MagicMock(),
        clock=_FrozenClock(after_grace).now,
    )
    runner.start()
    cb = captured["should_continue"]
    assert callable(cb)
    assert cb() is True
    runner._should_exit = True
    assert cb() is False


def test_runner_sets_quota_bucket_var_during_start():
    bucket = _bucket()
    captured: dict[str, object] = {}

    def daily_capture(*args, **kwargs):
        captured["bucket"] = QUOTA_BUCKET_VAR.get()

    daily_func = MagicMock(side_effect=daily_capture)
    after_grace = datetime.now(UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    config = RunnerConfig(
        scope=SCOPE_ALL_ACTIVE,
        granularities=frozenset({"daily"}),
        terminate_when_drained=True,
    )
    conn = _conn_returning_row((datetime.now(UTC),))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    runner = Runner(
        config=config, bucket=bucket,
        conn_factory=MagicMock(return_value=cm),
        run_daily_cycle=daily_func,
        run_minute_cycle=MagicMock(),
        clock=_FrozenClock(after_grace).now,
    )
    assert QUOTA_BUCKET_VAR.get() is None
    runner.start()
    assert captured["bucket"] is bucket
    assert QUOTA_BUCKET_VAR.get() is None  # reset on exit


def test_runner_explicit_symbols_passed_to_cycle():
    after_grace = datetime.now(UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    daily_func = MagicMock()
    bucket = _bucket()
    config = RunnerConfig(
        scope=("AAPL", "MSFT"),
        granularities=frozenset({"daily"}),
        terminate_when_drained=True,
    )
    conn = _conn_returning_row((datetime.now(UTC),))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    runner = Runner(
        config=config, bucket=bucket,
        conn_factory=MagicMock(return_value=cm),
        run_daily_cycle=daily_func,
        run_minute_cycle=MagicMock(),
        clock=_FrozenClock(after_grace).now,
    )
    runner.start()
    assert daily_func.call_count == 1
    args, kwargs = daily_func.call_args
    assert kwargs["symbols"] == ["AAPL", "MSFT"]
