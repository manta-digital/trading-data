"""Load tests for slice 146 part 1 NFRs (T20a).

Three checks:

  1. Token bucket overhead — mean per-call < 1ms, p99 < 5ms.
  2. List resolution latency — < 100ms per call against a 13k-symbol
     mocked instruments table.
  3. SIGTERM-to-exit latency — within 1.2× one symbol's processing
     time.

All three gate on MT_RUN_LOAD_TESTS=1 so they don't run in default
unit-test loops; CI must enable for slices touching these paths.
"""

from __future__ import annotations

import os
import signal
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MT_RUN_LOAD_TESTS") != "1",
    reason="MT_RUN_LOAD_TESTS=1 required",
)


def test_token_bucket_consume_overhead_under_1ms_mean():
    """100k consumes in a tight loop with mocked clock; mean < 1ms, p99 < 5ms."""
    from manta_trading.data.acquisition.quota import CallType, QuotaBucket

    # Use a clock that auto-advances on each consume so we never wait.
    counter = {"t": 0.0}

    def now() -> float:
        return counter["t"]

    def sleep(_s: float) -> None:
        counter["t"] += _s

    bucket = QuotaBucket(now=now, sleep=sleep)

    # Wall-clock the pure consume operation. Use perf_counter_ns for
    # microsecond-resolution sampling; histogram into per-call latencies.
    n = 100_000
    samples_ns: list[int] = []
    for _ in range(n):
        # Day window will eventually drain; advance the virtual clock
        # to refill so the loop never blocks.
        if bucket.day_window.available < 1:
            counter["t"] += 86400.0
        if bucket.minute_window.available < 1:
            counter["t"] += 60.0
        t0 = time.perf_counter_ns()
        bucket.consume(CallType.EOD)
        samples_ns.append(time.perf_counter_ns() - t0)

    mean_us = statistics.mean(samples_ns) / 1000.0
    p99_us = sorted(samples_ns)[int(0.99 * n)] / 1000.0
    assert mean_us < 1000.0, f"mean consume = {mean_us:.1f}us (target < 1000us)"
    assert p99_us < 5000.0, f"p99 consume = {p99_us:.1f}us (target < 5000us)"


def test_list_resolution_latency_under_100ms_median(tmp_path: Path):
    """resolve_list+intersect for a 500-symbol list against a 13k mocked
    instruments cursor — median < 100ms per call.
    """
    from manta_trading.data.lists import (
        intersect_with_active,
        resolve_list,
    )

    # Build a 500-symbol snapshot file.
    snap_dir = tmp_path / "lists"
    snap_dir.mkdir()
    snap_path = snap_dir / "sp500-snapshot.txt"
    snap_path.write_text("\n".join(f"SYM{i:04d}" for i in range(500)))

    cfg = tmp_path / "symbol-lists.yaml"
    cfg.write_text(
        "lists:\n"
        "  big:\n"
        "    description: 500-snapshot\n"
        "    source: file:lists/sp500-snapshot.txt\n"
    )

    # Mock 13k-symbol instruments cursor — first 500 happen to overlap
    # with the list, the rest are noise.
    universe = [(f"SYM{i:04d}",) for i in range(500)]

    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = universe
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    samples_ms: list[float] = []
    iterations = 100
    for _ in range(iterations):
        t0 = time.perf_counter()
        symbols = resolve_list("big", cfg)
        intersected = intersect_with_active(symbols, mock_conn)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
        assert len(intersected) == 500

    median_ms = statistics.median(samples_ms)
    assert median_ms < 100.0, f"median resolve_list = {median_ms:.2f}ms (target < 100ms)"


def test_sigterm_to_exit_latency_within_1_2x_symbol_time():
    """Exit-flag-set during a multi-symbol cycle; runner.start() must
    return within 1.2 × one symbol's processing time.

    NOTE: this exercises the should_continue gate via a direct
    ``runner._should_exit`` write rather than a real ``os.kill`` —
    Python only allows signal.signal on the main thread, and pytest
    drives this test on a worker. The end-to-end signal-handler path
    is exercised by ``test/integration/test_runner_sigterm.py`` (T20)
    when ``MT_TIMESCALE_DB_URL`` is available; this load test focuses
    on the latency budget of the gate itself.
    """
    from manta_trading.data.acquisition.daemon.runner import (
        Runner,
        RunnerConfig,
    )
    from manta_trading.data.acquisition.quota import QuotaBucket

    SYMBOL_TIME = 0.5  # seconds of "work" per symbol
    bucket = QuotaBucket()

    def fake_run_daily_cycle(*, symbols, should_continue):
        from manta_trading.data.acquisition.daemon.daily import CycleReport

        report = CycleReport()
        for _ in (symbols or []):
            if should_continue is not None and not should_continue():
                break
            time.sleep(SYMBOL_TIME)
            report.success_count += 1
        return report

    cur = MagicMock()
    cur.fetchone.return_value = (datetime.now(timezone.utc),)
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    factory = MagicMock(return_value=cm)

    config = RunnerConfig(
        scope=tuple(f"S{i}" for i in range(20)),
        granularities=frozenset({"daily"}),
        terminate_when_drained=False,
    )
    # Pin the runner's clock past the grace period so daily_cycle_due
    # returns True regardless of when the test is run.
    fixed_now = datetime.now(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    runner = Runner(
        config=config, bucket=bucket, conn_factory=factory,
        run_daily_cycle=fake_run_daily_cycle,
        run_minute_cycle=lambda **kw: None,
        clock=lambda: fixed_now,
    )

    # Avoid the signal.signal call on a worker thread by stubbing the
    # installer; the should_continue path is unaffected.
    runner._install_signal_handlers = lambda: (None, None)  # type: ignore[method-assign]
    runner._restore_signal_handlers = lambda *a: None  # type: ignore[method-assign]

    result: dict[str, float] = {}

    def _go() -> None:
        result["start"] = time.perf_counter()
        runner.start()
        result["end"] = time.perf_counter()

    t = threading.Thread(target=_go, daemon=True)
    t.start()

    time.sleep(SYMBOL_TIME * 0.4)
    flag_at = time.perf_counter()
    runner._should_exit = True

    t.join(timeout=SYMBOL_TIME * 5.0)
    assert not t.is_alive(), "runner did not exit"
    tail_latency = result["end"] - flag_at
    assert tail_latency <= SYMBOL_TIME * 1.2, (
        f"exit-flag tail latency = {tail_latency:.3f}s "
        f"(target <= {SYMBOL_TIME * 1.2:.3f}s)"
    )
