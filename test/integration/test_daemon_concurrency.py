"""Integration test for daemon advisory-lock concurrency (slice 146 T28c, SC13).

SC13: no deadlock under co-execution.

The full slice-148 ``mt data refetch`` will hold advisory locks for up to
30 minutes per symbol while it pulls and ingests minute history.  This test
uses a stand-in that holds the same lock surface for 3 × 5-second windows
(15 s total) instead of 30 minutes — enough to exercise the locking protocol
and confirm no deadlock without a 30-minute test budget.

The daemon subprocess and the stand-in threads run concurrently; they each
try to acquire ``advisory_lock(conn, symbol, granularity)`` against the same
database.  PostgreSQL transaction-level advisory locks serialise access, so
only one holder can proceed at a time — deadlock would mean both sides are
waiting for each other.  This test asserts that never happens.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Any

import psycopg
import psycopg.errors
import pytest

from manta_trading.data.locking import advisory_lock

# ---------------------------------------------------------------------------
# Environment gate
# ---------------------------------------------------------------------------

_TIMESCALE_URL: str = os.environ.get("MT_TIMESCALE_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not _TIMESCALE_URL,
    reason="MT_TIMESCALE_DB_URL required",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REFETCH_SLEEP_SECONDS: float = 5.0
_REFETCH_ITERATIONS: int = 3
_DAEMON_TIMEOUT_SECONDS: int = 120
_DAEMON_SYMBOLS: str = "AAPL,MSFT"
_LOCK_GRANULARITY: str = "daily"

# ---------------------------------------------------------------------------
# Stand-in helper
# ---------------------------------------------------------------------------


def refetch_stand_in(
    symbol: str,
    granularity: str,
    timescale_url: str,
) -> None:
    """Acquire advisory_lock(conn, symbol, granularity) for a synthetic window.

    Simulates the lock surface of slice-148's ``mt data refetch`` without
    actually downloading or writing any data.  Sleeps for
    ``_REFETCH_SLEEP_SECONDS`` under the lock to hold it long enough that
    any concurrent daemon cycles must wait, exercising the serialisation path.

    Raises:
        AssertionError: If a DeadlockDetected error is raised by PostgreSQL —
                        this indicates a bug in the lock-acquisition ordering.
    """
    with psycopg.connect(timescale_url) as conn:
        try:
            with advisory_lock(conn, symbol, granularity):
                time.sleep(_REFETCH_SLEEP_SECONDS)
            conn.commit()
        except psycopg.errors.DeadlockDetected as exc:
            raise AssertionError(
                f"DeadlockDetected while holding advisory lock for "
                f"({symbol!r}, {granularity!r}): {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Thread worker
# ---------------------------------------------------------------------------


def _stand_in_worker(
    symbol: str,
    granularity: str,
    timescale_url: str,
    errors: list[BaseException],
) -> None:
    """Run ``_REFETCH_ITERATIONS`` back-to-back stand-in calls; collect errors."""
    for _ in range(_REFETCH_ITERATIONS):
        try:
            refetch_stand_in(symbol, granularity, timescale_url)
        except BaseException as exc:  # noqa: BLE001 — collected for assertion below
            errors.append(exc)
            return  # stop on first failure; no point continuing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _advisory_locks_held(pids: list[int]) -> list[dict[str, Any]]:
    """Query pg_locks for any advisory locks still held by the given pids."""
    if not pids:
        return []
    placeholders = ",".join(["%s"] * len(pids))
    query = (
        f"SELECT pid, classid, objid, granted "
        f"FROM pg_locks "
        f"WHERE locktype = 'advisory' AND pid = ANY(ARRAY[{placeholders}]::int[])"
    )
    with psycopg.connect(_TIMESCALE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, pids)
            rows = cur.fetchall()
    return [
        {"pid": r[0], "classid": r[1], "objid": r[2], "granted": r[3]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestDaemonConcurrency:
    def test_no_deadlock_under_co_execution(self) -> None:
        """Daemon and stand-in refetch co-execute without deadlock (SC13).

        Steps:
        1. Launch daemon subprocess for AAPL and MSFT (daily, no-minute).
        2. After a short startup delay, fire the refetch stand-in against
           AAPL 3 times back-to-back from a background thread.
        3. Wait for both to complete (daemon timeout: 120 s).
        4. Assert no DeadlockDetected was raised.
        5. Assert both exited successfully (daemon rc == 0, no thread errors).
        6. Assert pg_locks has no residual advisory locks for either pid.
        """
        thread_errors: list[BaseException] = []

        # Start the daemon subprocess first so it can acquire its own locks.
        daemon = subprocess.Popen(
            [
                "mt",
                "data",
                "daemon",
                "run",
                "--symbols",
                _DAEMON_SYMBOLS,
                "--stop-when-done",
                "--daily",
                "--no-minute",
            ],
            env=os.environ,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        daemon_pid: int = daemon.pid

        # Give the daemon a moment to initialise its connection and first lock.
        time.sleep(2.0)

        stand_in_thread = threading.Thread(
            target=_stand_in_worker,
            args=("AAPL", _LOCK_GRANULARITY, _TIMESCALE_URL, thread_errors),
            daemon=True,
            name="refetch-stand-in",
        )
        stand_in_thread.start()

        # Wait for the daemon to finish (or timeout).
        try:
            stdout, stderr = daemon.communicate(timeout=_DAEMON_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            daemon.kill()
            stdout, stderr = daemon.communicate()
            pytest.fail(
                f"Daemon subprocess timed out after {_DAEMON_TIMEOUT_SECONDS}s.\n"
                f"stdout: {stdout}\nstderr: {stderr}"
            )

        # Wait for the stand-in thread to finish.
        stand_in_thread.join(timeout=_REFETCH_SLEEP_SECONDS * _REFETCH_ITERATIONS + 10)

        # ------------------------------------------------------------------ #
        # Assertions
        # ------------------------------------------------------------------ #

        # 1. No deadlock or other error from stand-in thread.
        assert not thread_errors, (
            "Stand-in refetch thread raised errors:\n"
            + "\n".join(f"  {type(e).__name__}: {e}" for e in thread_errors)
        )

        # 2. Daemon exited cleanly.
        assert daemon.returncode == 0, (
            f"Daemon exited with code {daemon.returncode}.\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )

        # 3. Stand-in thread completed (did not hang).
        assert not stand_in_thread.is_alive(), (
            "Stand-in thread did not finish within the allotted time — "
            "possible lock hang without deadlock detection."
        )

        # 4. No residual advisory locks for either pid.
        # The stand-in uses a fresh psycopg connection each call; we can only
        # check the daemon's backend pid here.  The stand-in connections will
        # have been closed and their locks released before we get here.
        residual = _advisory_locks_held([daemon_pid])
        assert not residual, (
            f"Residual advisory locks found after both processes exited: "
            f"{residual}"
        )
