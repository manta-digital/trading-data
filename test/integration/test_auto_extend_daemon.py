"""Integration test: auto-extension triggered via daemon idle tick (slice 147 T14).

Runs ``mt data daemon run --symbols SPY --daily --stop-when-done`` as a
subprocess against live infrastructure to verify that the idle hook extends
the trading_sessions horizon when it is short.

Skipped when MT_TIMESCALE_DB_URL or MT_EODHD_API_KEY are absent.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta

import psycopg
import pytest

_TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")
_EODHD_API_KEY = os.environ.get("MT_EODHD_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not _TIMESCALE_URL or not _EODHD_API_KEY,
    reason="MT_TIMESCALE_DB_URL and MT_EODHD_API_KEY required",
)

_MT = [sys.executable, "-m", "manta_trading.cli"]
_CALENDAR = "NYSE"


def _max_session_date() -> date | None:
    with psycopg.connect(_TIMESCALE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(session_date) FROM trading_sessions "
                "WHERE calendar_id = %s",
                (_CALENDAR,),
            )
            row = cur.fetchone()
    return row[0] if row and row[0] else None


def _truncate_horizon(days_ahead: int = 30) -> None:
    with psycopg.connect(_TIMESCALE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM trading_sessions "
                "WHERE calendar_id = %s AND session_date > current_date + %s",
                (_CALENDAR, days_ahead),
            )
        conn.commit()


def _run_daemon_once() -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_MT, "data", "daemon", "run", "--symbols", "SPY", "--daily", "--stop-when-done"],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_daemon_extends_short_horizon() -> None:
    """Daemon idle hook extends the NYSE horizon when it is within 30 days."""
    _truncate_horizon(30)

    result = _run_daemon_once()
    assert result.returncode == 0, f"daemon failed:\n{result.stderr}"

    max_date = _max_session_date()
    assert max_date is not None
    assert max_date > date.today() + timedelta(days=90), (
        f"Horizon not extended; max_date={max_date}"
    )


def test_daemon_noop_hook_on_healthy_horizon() -> None:
    """When the horizon is already healthy, the idle hook is a no-op (no errors)."""
    # Ensure the horizon is healthy (previous test should have extended it).
    max_before = _max_session_date()

    result = _run_daemon_once()
    assert result.returncode == 0, f"daemon failed:\n{result.stderr}"

    max_after = _max_session_date()
    # The horizon should not have regressed (it's still healthy).
    if max_before is not None and max_after is not None:
        assert max_after >= max_before
