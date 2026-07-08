"""Unit tests for Runner.register_idle_hook (slice 147 T12)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from manta_trading.data.acquisition.daemon.runner import (
    SCOPE_ALL_ACTIVE,
    Runner,
    RunnerConfig,
    RunnerState,
)
from manta_trading.data.acquisition.quota import QuotaBucket

UTC = timezone.utc


def _bucket() -> QuotaBucket:
    return QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)


def _conn_with_today_ca() -> MagicMock:
    """Conn mock whose ca_update_due check returns 'updated today' → not due."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (datetime.now(UTC),)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_cm
    return cm


def _make_runner(*, terminate_when_drained: bool = True) -> Runner:
    bucket = _bucket()
    conn_factory = MagicMock(return_value=_conn_with_today_ca())
    config = RunnerConfig(
        scope=SCOPE_ALL_ACTIVE,
        granularities=frozenset(),  # no cycles — so hooks fire on every "idle" pass
        terminate_when_drained=terminate_when_drained,
    )
    return Runner(
        config=config,
        bucket=bucket,
        conn_factory=conn_factory,
        run_daily_cycle=MagicMock(),
        run_minute_cycle=MagicMock(),
        run_ca_update=MagicMock(),
        clock=lambda: datetime.now(UTC).replace(hour=12),
        sleep=lambda _s: None,
    )


def test_hook_called_between_cycles() -> None:
    """A registered hook is called at least once during a drain run."""
    runner = _make_runner()
    call_count: list[int] = [0]

    def my_hook() -> None:
        call_count[0] += 1

    runner.register_idle_hook(my_hook)
    runner.start()

    assert call_count[0] >= 1


def test_hook_exception_does_not_crash_runner() -> None:
    """A hook that raises RuntimeError does not prevent the runner from exiting normally."""

    runner = _make_runner()

    def bad_hook() -> None:
        raise RuntimeError("boom")

    runner.register_idle_hook(bad_hook)
    exit_code = runner.start()
    assert exit_code == 0


def test_no_hooks_no_op() -> None:
    """Runner with no hooks registered completes normally."""
    runner = _make_runner()
    exit_code = runner.start()
    assert exit_code == 0
