"""Load tests for slice 146 part 2 NFRs (T28a).

Two checks:

  1. Throughput, single-symbol fast path — in-process Runner with mocked
     cycle functions and mocked conn completes within 120s wall clock.

  2. Memory at universe scope — in-process Runner with mocked cycle
     functions; one pass (terminate_when_drained=True); RSS increase < 200 MB.

Both gate on MT_RUN_LOAD_TESTS=1.
"""

from __future__ import annotations

import contextlib
import os
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import psutil
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MT_RUN_LOAD_TESTS") != "1",
    reason="MT_RUN_LOAD_TESTS=1 required",
)

_UTC = timezone.utc


def _make_conn_factory(fetchone_result: object) -> MagicMock:
    """Return a conn_factory mock whose cursor().fetchone() returns
    ``fetchone_result``.

    Uses contextlib.contextmanager to avoid MagicMock context-manager
    recursion issues on Python 3.12.
    """
    # Build a real context manager for the cursor so ``with conn.cursor() as
    # cur:`` works without relying on MagicMock's __enter__/__exit__ chaining.
    @contextlib.contextmanager  # type: ignore[misc]
    def _cursor_cm():  # type: ignore[return]
        cur = MagicMock()
        cur.fetchone.return_value = fetchone_result
        yield cur

    conn = MagicMock()
    conn.cursor.return_value = _cursor_cm()

    @contextlib.contextmanager  # type: ignore[misc]
    def _conn_cm():  # type: ignore[return]
        yield conn

    factory = MagicMock(return_value=_conn_cm())
    return factory


def _make_runner(
    scope: tuple[str, ...],
    granularities: frozenset[str],
    terminate_when_drained: bool,
    fixed_now: datetime,
    daily_fn: object,
    minute_fn: object,
) -> object:
    """Construct a Runner with injected mocks; stub signal handlers."""
    from manta_trading.data.acquisition.daemon.runner import Runner, RunnerConfig
    from manta_trading.data.acquisition.quota import QuotaBucket

    # Return a timestamp in the past so ca_update_due evaluates to False
    # (past date < today → True would trigger CA update; use today to suppress).
    today_ts = fixed_now
    conn_factory = _make_conn_factory(fetchone_result=(today_ts,))

    config = RunnerConfig(
        scope=scope,
        granularities=granularities,
        terminate_when_drained=terminate_when_drained,
    )
    bucket = QuotaBucket()
    runner = Runner(
        config=config,
        bucket=bucket,
        conn_factory=conn_factory,
        run_daily_cycle=daily_fn,
        run_minute_cycle=minute_fn,
        clock=lambda: fixed_now,
    )
    runner._install_signal_handlers = lambda: (None, None)  # type: ignore[method-assign]
    runner._restore_signal_handlers = lambda *a: None  # type: ignore[method-assign]
    return runner


def test_single_symbol_fast_path_wall_clock_under_120s() -> None:
    """Runner with mocked HTTP (fast daily cycle) for a single symbol must
    complete within 120 s wall clock.

    Uses ``terminate_when_drained=True`` so the runner exits after the daily
    cycle runs once and no further cycles are due.  The cycle function itself
    returns immediately (zero I/O), so any overhead above a few milliseconds
    reflects framework bookkeeping — the 120 s budget is the spec ceiling.
    """
    from manta_trading.data.acquisition.daemon.daily import CycleReport

    fake_report = CycleReport(success_count=1)

    def fast_daily_cycle(
        *, symbols: list[str] | None, should_continue: object
    ) -> CycleReport:
        return fake_report

    def fast_minute_cycle(
        *, symbols: list[str] | None, should_continue: object
    ) -> CycleReport:
        return fake_report

    fixed_now = datetime.now(_UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    runner = _make_runner(
        scope=("SPY",),
        granularities=frozenset({"daily"}),
        terminate_when_drained=True,
        fixed_now=fixed_now,
        daily_fn=fast_daily_cycle,
        minute_fn=fast_minute_cycle,
    )

    t0 = time.perf_counter()
    exit_code = runner.start()  # type: ignore[union-attr]
    elapsed = time.perf_counter() - t0

    assert exit_code == 0, f"runner exited with non-zero code: {exit_code}"
    assert elapsed <= 120.0, (
        f"single-symbol fast path took {elapsed:.2f}s (target <= 120s)"
    )


def test_memory_rss_increase_under_200mb_one_pass() -> None:
    """In-process Runner with mocked cycles; one pass via
    terminate_when_drained=True.  RSS increase must be < 200 MB.

    This is a one-pass proxy for the full 500 MB / 5-minute NFR; a single
    pass exercises the hot path allocations without steady-state accumulation.
    """
    from manta_trading.data.acquisition.daemon.daily import CycleReport

    fake_report = CycleReport(success_count=500)

    def noop_daily(
        *, symbols: list[str] | None, should_continue: object
    ) -> CycleReport:
        return fake_report

    def noop_minute(
        *, symbols: list[str] | None, should_continue: object
    ) -> CycleReport:
        return fake_report

    fixed_now = datetime.now(_UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    # Large explicit scope to exercise memory behaviour across many symbols.
    large_scope = tuple(f"SYM{i:04d}" for i in range(500))
    runner = _make_runner(
        scope=large_scope,
        granularities=frozenset({"daily", "minute"}),
        terminate_when_drained=True,
        fixed_now=fixed_now,
        daily_fn=noop_daily,
        minute_fn=noop_minute,
    )

    proc = psutil.Process()
    rss_before = proc.memory_info().rss

    runner.start()  # type: ignore[union-attr]

    rss_after = proc.memory_info().rss
    rss_increase_mb = (rss_after - rss_before) / (1024 * 1024)

    assert rss_increase_mb < 200.0, (
        f"RSS increased by {rss_increase_mb:.1f} MB after one pass "
        f"(target < 200 MB)"
    )
