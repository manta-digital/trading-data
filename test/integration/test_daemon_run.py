"""Integration test for daemon run end-to-end (slice 146 T28).

Runs ``mt data daemon run --symbols SPY --stop-when-done`` as a real
subprocess against live infrastructure (TimescaleDB + EODHD).

Skipped when MT_TIMESCALE_DB_URL or MT_EODHD_API_KEY are absent so the
test never blocks CI environments that lack credentials.
"""

from __future__ import annotations

import os
import subprocess

import psycopg
import pytest

from manta_trading.data.acquisition.state import Granularity, LastAttemptOutcome

_TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")
_EODHD_API_KEY = os.environ.get("MT_EODHD_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not _TIMESCALE_URL or not _EODHD_API_KEY,
    reason="MT_TIMESCALE_DB_URL and MT_EODHD_API_KEY required",
)

_SYMBOL = "SPY"


def _reset_spy_daily_state() -> None:
    """Remove SPY's daily acquisition_state row so the daemon fetches fresh."""
    with psycopg.connect(_TIMESCALE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM acquisition_state"
                " WHERE symbol = %s AND granularity = %s",
                (_SYMBOL, str(Granularity.DAILY)),
            )
        conn.commit()


def _count_spy_daily_ohlcv() -> int:
    with psycopg.connect(_TIMESCALE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM daily_ohlcv WHERE symbol = %s",
                (_SYMBOL,),
            )
            row = cur.fetchone()
    return int(row[0]) if row else 0


def _fetch_spy_daily_outcome() -> str | None:
    with psycopg.connect(_TIMESCALE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_attempt_outcome FROM acquisition_state"
                " WHERE symbol = %s AND granularity = %s"
                " LIMIT 1",
                (_SYMBOL, str(Granularity.DAILY)),
            )
            row = cur.fetchone()
    return str(row[0]) if row and row[0] is not None else None


class TestDaemonRunHappyPath:
    def test_spy_daily_ingested(self) -> None:
        """Daemon fetches SPY daily OHLCV and records a success outcome."""
        _reset_spy_daily_state()

        result = subprocess.run(
            [
                "mt",
                "data",
                "daemon",
                "run",
                "--symbols",
                _SYMBOL,
                "--stop-when-done",
                "--daily",
                "--no-minute",
            ],
            env=os.environ,
            timeout=300,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            f"daemon exited {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        row_count = _count_spy_daily_ohlcv()
        assert row_count > 0, (
            f"daily_ohlcv has no rows for {_SYMBOL} after daemon run"
        )

        outcome = _fetch_spy_daily_outcome()
        assert outcome == str(LastAttemptOutcome.SUCCESS), (
            f"acquisition_state outcome for {_SYMBOL}/daily is {outcome!r},"
            f" expected {LastAttemptOutcome.SUCCESS!r}"
        )
