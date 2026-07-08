"""Unit tests for DaemonStatus, DaemonHeartbeat, and HeartbeatRepository.

Uses a FakeHeartbeatRepo (in-memory dict) — no real database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from manta_trading.data.acquisition.daemon.heartbeat import (
    DaemonHeartbeat,
    DaemonStatus,
    HeartbeatRepository,
)
from manta_trading.data.acquisition.daemon.types import (
    DAILY_DAEMON_ID,
    HEARTBEAT_ALIVE_THRESHOLD_SECONDS,
)


# ---------------------------------------------------------------------------
# Fake repository (no DB)
# ---------------------------------------------------------------------------


class FakeHeartbeatRepo:
    """In-memory replacement for HeartbeatRepository — used in unit tests only."""

    def __init__(self) -> None:
        self._store: dict[str, DaemonHeartbeat] = {}

    def upsert(self, heartbeat: DaemonHeartbeat) -> None:
        self._store[heartbeat.daemon_id] = heartbeat

    def get(self, daemon_id: str) -> DaemonHeartbeat | None:
        return self._store.get(daemon_id)

    def is_alive(
        self,
        heartbeat: DaemonHeartbeat | None,
        *,
        threshold_seconds: int = HEARTBEAT_ALIVE_THRESHOLD_SECONDS,
    ) -> bool:
        if heartbeat is None:
            return False
        now = datetime.now(timezone.utc)
        beat = heartbeat.last_beat_at
        if beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        age = (now - beat).total_seconds()
        return age <= threshold_seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_heartbeat(
    status: DaemonStatus = DaemonStatus.WORKING,
    last_beat_at: datetime | None = None,
    cycle_count: int = 0,
) -> DaemonHeartbeat:
    now = datetime.now(timezone.utc)
    return DaemonHeartbeat(
        daemon_id=DAILY_DAEMON_ID,
        status=status,
        started_at=now,
        last_beat_at=last_beat_at if last_beat_at is not None else now,
        cycle_count=cycle_count,
    )


# ---------------------------------------------------------------------------
# FakeHeartbeatRepo tests
# ---------------------------------------------------------------------------


def test_upsert_creates_row() -> None:
    repo = FakeHeartbeatRepo()
    hb = _make_heartbeat(status=DaemonStatus.STARTING)
    repo.upsert(hb)
    result = repo.get(DAILY_DAEMON_ID)
    assert result is not None
    assert result.daemon_id == DAILY_DAEMON_ID
    assert result.status == DaemonStatus.STARTING
    assert result.cycle_count == 0


def test_upsert_updates_existing() -> None:
    repo = FakeHeartbeatRepo()
    repo.upsert(_make_heartbeat(status=DaemonStatus.STARTING, cycle_count=0))
    repo.upsert(_make_heartbeat(status=DaemonStatus.CYCLE_COMPLETE, cycle_count=1))
    result = repo.get(DAILY_DAEMON_ID)
    assert result is not None
    assert result.status == DaemonStatus.CYCLE_COMPLETE
    assert result.cycle_count == 1
    # Only one row for this daemon_id
    assert len(repo._store) == 1


def test_is_alive_within_threshold() -> None:
    repo = FakeHeartbeatRepo()
    recent = datetime.now(timezone.utc) - timedelta(seconds=10)
    hb = _make_heartbeat(last_beat_at=recent)
    assert repo.is_alive(hb, threshold_seconds=300) is True


def test_is_alive_expired() -> None:
    repo = FakeHeartbeatRepo()
    stale = datetime.now(timezone.utc) - timedelta(seconds=600)
    hb = _make_heartbeat(last_beat_at=stale)
    assert repo.is_alive(hb, threshold_seconds=300) is False


def test_is_alive_no_row() -> None:
    repo = FakeHeartbeatRepo()
    assert repo.is_alive(None) is False


# ---------------------------------------------------------------------------
# DaemonStatus enum coverage
# ---------------------------------------------------------------------------


def test_daemon_status_covers_all_values() -> None:
    """Confirm all expected status values exist as enum members."""
    expected = {"STARTING", "WORKING", "IDLE", "CYCLE_COMPLETE", "STOPPED"}
    actual = {member.name for member in DaemonStatus}
    assert actual == expected


def test_daemon_status_values_are_strings() -> None:
    """StrEnum values should equal their string representation."""
    for member in DaemonStatus:
        assert str(member) == member.value


def test_daemon_status_no_bare_strings_in_heartbeat() -> None:
    """DaemonHeartbeat must accept DaemonStatus, not raw strings, for status."""
    hb = _make_heartbeat(status=DaemonStatus.IDLE)
    # status field should be the enum, not a plain string
    assert isinstance(hb.status, DaemonStatus)


# ---------------------------------------------------------------------------
# HeartbeatRepository.is_alive (real implementation, no DB needed)
# ---------------------------------------------------------------------------


def test_real_is_alive_within_threshold() -> None:
    """Test is_alive via the real HeartbeatRepository (no pool needed — pure logic)."""
    # We can call is_alive directly without a real pool since it only uses self._pool
    # indirectly via upsert/get. Instantiate with None and only call is_alive.
    # This is safe because is_alive does not access self._pool.
    repo = HeartbeatRepository(pool=None)  # type: ignore[arg-type]
    recent = datetime.now(timezone.utc) - timedelta(seconds=10)
    hb = _make_heartbeat(last_beat_at=recent)
    assert repo.is_alive(hb, threshold_seconds=300) is True


def test_real_is_alive_expired() -> None:
    repo = HeartbeatRepository(pool=None)  # type: ignore[arg-type]
    stale = datetime.now(timezone.utc) - timedelta(seconds=600)
    hb = _make_heartbeat(last_beat_at=stale)
    assert repo.is_alive(hb, threshold_seconds=300) is False


def test_real_is_alive_none() -> None:
    repo = HeartbeatRepository(pool=None)  # type: ignore[arg-type]
    assert repo.is_alive(None) is False
