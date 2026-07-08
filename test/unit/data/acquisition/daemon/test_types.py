"""Unit tests for shared daemon types and constants."""

from __future__ import annotations

from manta_trading.data.acquisition.daemon.types import DAILY_DAEMON_ID, MINUTE_DAEMON_ID


def test_minute_daemon_id_distinct_from_daily() -> None:
    """MINUTE_DAEMON_ID and DAILY_DAEMON_ID must be distinct (separate heartbeat rows)."""
    assert MINUTE_DAEMON_ID != DAILY_DAEMON_ID


def test_minute_daemon_id_value() -> None:
    """MINUTE_DAEMON_ID must equal the canonical string contract with the heartbeat table."""
    assert MINUTE_DAEMON_ID == "minute-acquisition"
