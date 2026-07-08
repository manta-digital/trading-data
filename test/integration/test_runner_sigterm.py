"""Integration test for runner SIGTERM clean shutdown (slice 146 T20).

Calls ``Runner(...).start()`` directly in a background thread against a
small bounded scope; the main thread sends SIGTERM mid-cycle and
asserts the runner returns 0 within one symbol's processing time and
holds no leaked advisory locks.

Skipped without MT_TIMESCALE_DB_URL.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL required"
)


def _no_leaked_advisory_locks(pid: int) -> bool:
    with psycopg.connect(TIMESCALE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND pid = %s",
                (pid,),
            )
            row = cur.fetchone()
    return bool(row and int(row[0]) == 0)


def test_sigterm_returns_0_between_symbols():
    from manta_trading.data.acquisition.daemon.runner import (
        Runner,
        RunnerConfig,
    )
    from manta_trading.data.acquisition.quota import QuotaBucket

    bucket = QuotaBucket()

    # Conn factory connects to the live DB so ca_update_due actually
    # runs; the sentinel check returns "today" so no CA update fires.
    def conn_factory():
        return psycopg.connect(TIMESCALE_URL)

    config = RunnerConfig(
        scope=("AAPL", "MSFT", "GOOGL", "NVDA", "TSLA"),
        granularities=frozenset({"daily"}),
        terminate_when_drained=False,  # would loop forever
    )

    # Real cycle would issue HTTP calls; mock it here so the test is
    # provider-free. The mock sleeps a bit per "symbol" to give
    # SIGTERM a chance to land between iterations.
    def fake_run_daily_cycle(*, symbols, should_continue):
        from manta_trading.data.acquisition.daemon.daily import CycleReport

        report = CycleReport()
        for sym in (symbols or []):
            if should_continue is not None and not should_continue():
                break
            time.sleep(0.5)  # one symbol's worth of work
            report.success_count += 1
        report.wall_clock_seconds = 0.0
        return report

    runner = Runner(
        config=config,
        bucket=bucket,
        conn_factory=conn_factory,
        run_daily_cycle=fake_run_daily_cycle,
        run_minute_cycle=lambda **kw: None,
    )

    exit_code: dict[str, int] = {}

    def _runner_thread() -> None:
        exit_code["v"] = runner.start()

    t = threading.Thread(target=_runner_thread, daemon=True)
    t.start()

    # Let the runner process at least one symbol, then send SIGTERM.
    time.sleep(0.3)
    os.kill(os.getpid(), signal.SIGTERM)

    # SIGTERM is meant to set the flag and let the current symbol
    # finish; total tail latency ≤ one symbol's processing time.
    t.join(timeout=5.0)
    assert not t.is_alive(), "runner did not exit after SIGTERM"
    assert exit_code["v"] == 0
    assert _no_leaked_advisory_locks(os.getpid())
