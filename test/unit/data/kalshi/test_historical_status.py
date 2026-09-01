"""``historical_status`` and the effective floor without a database (slice
267, Task 7.3). Row outcomes are the integration tier's
(``test_kalshi_status.py``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from kalshi_support.fake_status_conn import FakeStatusConn

from manta_trading.data.kalshi.constants import HISTORICAL_TRADES_FLOOR, Surface
from manta_trading.data.kalshi.historical_status import (
    HistoricalStatus,
    read_historical_status,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
LIVE_FLOOR = datetime(2026, 7, 1, tzinfo=UTC)
FLOOR = HISTORICAL_TRADES_FLOOR


def _rows(
    historical: tuple[Any, ...] | None, live: tuple[Any, ...] | None
) -> dict[str, tuple[Any, ...] | None]:
    return {Surface.HISTORICAL.value: historical, Surface.TRADES.value: live}


class TestReadHistoricalStatus:
    def test_none_without_a_row(self):
        conn = FakeStatusConn(_rows(None, (NOW, NOW, LIVE_FLOOR, None)))
        assert read_historical_status(conn.as_connection()) is None

    def test_walk_in_progress(self):
        conn = FakeStatusConn(_rows((NOW, None, None, "archive-7"), None))
        status = read_historical_status(conn.as_connection())
        assert status == HistoricalStatus(
            last_phase_at=NOW,
            archive_walked=False,
            archive_in_progress=True,
            tape_from=None,
            tape_to=None,
            floor=FLOOR,
            floor_reached=False,
        )

    def test_descending(self):
        tape_from = LIVE_FLOOR - timedelta(days=10)
        conn = FakeStatusConn(
            _rows((NOW, tape_from, FLOOR, None), (NOW, NOW, LIVE_FLOOR, None))
        )
        status = read_historical_status(conn.as_connection())
        assert status is not None
        assert status.archive_walked is True and status.archive_in_progress is False
        assert status.tape_from == tape_from and status.tape_to == LIVE_FLOOR
        assert status.floor == FLOOR and status.floor_reached is False
        assert status.to_dict() == {
            "last_phase_at": NOW.isoformat(),
            "archive_walked": True,
            "archive_in_progress": False,
            "tape_from": tape_from.isoformat(),
            "tape_to": LIVE_FLOOR.isoformat(),
            "floor": FLOOR.isoformat(),
            "floor_reached": False,
        }

    def test_floor_reached(self):
        conn = FakeStatusConn(
            _rows((NOW, FLOOR, FLOOR, None), (NOW, NOW, LIVE_FLOOR, None))
        )
        status = read_historical_status(conn.as_connection())
        assert status is not None and status.floor_reached is True
        assert [p["surface"] for p in conn.params] == [
            Surface.HISTORICAL.value,
            Surface.TRADES.value,
        ]
