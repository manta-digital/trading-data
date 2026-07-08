"""Integration test for runner CA update once-per-UTC-day gate (slice 146 T28b).

Runs ``mt data daemon run`` as a subprocess twice in the same UTC day and
asserts:
  1. First run: sentinel row ``('__bulk_ca__', 'daily')`` gets a non-NULL
     ``last_attempt_ts``.
  2. Second run: sentinel ``last_attempt_ts`` is not updated (DB-backed gate
     held).

Skipped without MT_TIMESCALE_DB_URL.
"""

from __future__ import annotations

import os
import subprocess
import time

import psycopg
import pytest

from manta_trading.data.acquisition.daemon.runner import (
    CA_UPDATE_SENTINEL_GRANULARITY,
    CA_UPDATE_SENTINEL_SYMBOL,
)

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL required"
)

_DAEMON_CMD = [
    "mt",
    "data",
    "daemon",
    "run",
    "--symbols",
    "AAPL",
    "--stop-when-done",
    "--daily",
    "--no-minute",
]
_DAEMON_TIMEOUT = 300


def _reset_sentinel(conn: psycopg.Connection) -> None:
    """Set last_attempt_ts to NULL on the CA sentinel row (or delete it)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE acquisition_state
               SET last_attempt_ts = NULL
             WHERE symbol = %s
               AND granularity = %s
            """,
            (CA_UPDATE_SENTINEL_SYMBOL, CA_UPDATE_SENTINEL_GRANULARITY),
        )
        if cur.rowcount == 0:
            # Row may not exist yet; that is fine — the runner will create it.
            pass
    conn.commit()


def _fetch_last_attempt_ts(conn: psycopg.Connection) -> float | None:
    """Return last_attempt_ts as a POSIX timestamp, or None if row absent / NULL."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT last_attempt_ts
              FROM acquisition_state
             WHERE symbol = %s
               AND granularity = %s
            """,
            (CA_UPDATE_SENTINEL_SYMBOL, CA_UPDATE_SENTINEL_GRANULARITY),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0].timestamp()


def _run_daemon() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _DAEMON_CMD,
        env=os.environ,
        timeout=_DAEMON_TIMEOUT,
        capture_output=True,
        text=True,
    )


class TestCaUpdateGate:
    """Verify the once-per-UTC-day CA update gate backed by acquisition_state."""

    def test_first_run_fires_ca_update(self) -> None:
        """First run after sentinel reset must write a non-NULL last_attempt_ts."""
        with psycopg.connect(TIMESCALE_URL) as conn:
            _reset_sentinel(conn)

        result = _run_daemon()
        assert result.returncode == 0, (
            f"daemon exited {result.returncode}\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        with psycopg.connect(TIMESCALE_URL) as conn:
            ts = _fetch_last_attempt_ts(conn)

        assert ts is not None, (
            "CA sentinel last_attempt_ts is still NULL after first daemon run; "
            "ca_update step did not fire."
        )

    def test_second_run_does_not_update_sentinel(self) -> None:
        """Second run in the same UTC day must not advance last_attempt_ts."""
        with psycopg.connect(TIMESCALE_URL) as conn:
            _reset_sentinel(conn)

        # First run — establishes the sentinel timestamp.
        result1 = _run_daemon()
        assert result1.returncode == 0, (
            f"first daemon run exited {result1.returncode}\n"
            f"stdout: {result1.stdout}\nstderr: {result1.stderr}"
        )

        with psycopg.connect(TIMESCALE_URL) as conn:
            ts_after_first = _fetch_last_attempt_ts(conn)

        assert ts_after_first is not None, (
            "CA sentinel last_attempt_ts is NULL after first run; "
            "cannot verify gate behaviour."
        )

        # Brief pause so wall-clock advances slightly, making any accidental
        # update detectable.
        time.sleep(2)

        # Second run — gate should prevent another CA update.
        result2 = _run_daemon()
        assert result2.returncode == 0, (
            f"second daemon run exited {result2.returncode}\n"
            f"stdout: {result2.stdout}\nstderr: {result2.stderr}"
        )

        with psycopg.connect(TIMESCALE_URL) as conn:
            ts_after_second = _fetch_last_attempt_ts(conn)

        assert ts_after_second is not None, (
            "CA sentinel last_attempt_ts unexpectedly NULL after second run."
        )

        delta = abs(ts_after_second - ts_after_first)
        assert delta < 3.0, (
            f"CA sentinel advanced by {delta:.3f}s between runs; "
            "DB-backed gate did not hold — ca_update fired a second time."
        )
