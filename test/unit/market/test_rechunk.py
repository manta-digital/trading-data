"""Unit tests for the slice 166 rechunk driver's pure logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from manta_trading.constants import MINUTE_OHLCV_CHUNK_INTERVAL
from manta_trading.market.maintenance.rechunk import (
    MINUTE_CAGG_GRANULARITIES,
    WindowState,
    _load_windows,
    _window_start,
)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestWindowStart:
    """Windows must sit on TimescaleDB's epoch-anchored grid (1970-01-01 + k*interval),
    because that is where tuple routing places fresh 7-day chunk slices."""

    def test_epoch_is_its_own_window_start(self) -> None:
        assert _window_start(_utc(1970, 1, 1), timedelta(days=7)) == _utc(1970, 1, 1)

    def test_known_grid_boundary(self) -> None:
        # 2025-01-02 is 20090 days after 1970-01-01; 20090 % 7 == 0.
        assert _window_start(_utc(2025, 1, 2), timedelta(days=7)) == _utc(2025, 1, 2)

    def test_mid_window_timestamp_floors_to_grid(self) -> None:
        # Any instant during 2025-01-06 (a Monday) belongs to the grid week
        # starting Thursday 2025-01-02.
        assert _window_start(_utc(2025, 1, 6, 15, 30), timedelta(days=7)) == _utc(2025, 1, 2)

    def test_window_end_minus_epsilon_stays_in_window(self) -> None:
        end = _utc(2025, 1, 9)
        assert _window_start(end - timedelta(seconds=1), timedelta(days=7)) == _utc(2025, 1, 2)
        assert _window_start(end, timedelta(days=7)) == _utc(2025, 1, 9)


def _mock_conn_with_chunks(rows: list[dict]) -> MagicMock:
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = rows
    return conn


def _chunk(name: str, start: datetime, hours: int, compressed: bool) -> dict:
    return {
        "chunk": f"_timescaledb_internal.{name}",
        "range_start": start,
        "range_end": start + timedelta(hours=hours),
        "is_compressed": compressed,
    }


class TestLoadWindowsClassification:
    INTERVAL = MINUTE_OHLCV_CHUNK_INTERVAL  # 7 days
    GRID = _utc(2025, 1, 2)  # known grid boundary (see TestWindowStart)

    def _windows(self, rows: list[dict]):
        return _load_windows(_mock_conn_with_chunks(rows), "t", self.INTERVAL)

    def test_multiple_compressed_chunks_classify_rewrite(self) -> None:
        rows = [
            _chunk("c1", self.GRID + timedelta(hours=12), 4, True),
            _chunk("c2", self.GRID + timedelta(hours=16), 4, True),
        ]
        (w,) = self._windows(rows)
        assert w.state == WindowState.REWRITE
        assert w.start == self.GRID
        assert w.end == self.GRID + self.INTERVAL
        assert len(w.chunks) == 2

    def test_single_aligned_compressed_chunk_is_done(self) -> None:
        rows = [_chunk("c1", self.GRID, 7 * 24, True)]
        (w,) = self._windows(rows)
        assert w.state == WindowState.DONE

    def test_single_aligned_uncompressed_chunk_is_compress_only(self) -> None:
        """The crash-between-commit-and-compress resume case."""
        rows = [_chunk("c1", self.GRID, 7 * 24, False)]
        (w,) = self._windows(rows)
        assert w.state == WindowState.COMPRESS_ONLY

    def test_any_uncompressed_in_multichunk_window_skips(self) -> None:
        """Trailing chunks inside the compress_after horizon are left alone."""
        rows = [
            _chunk("c1", self.GRID + timedelta(hours=12), 4, True),
            _chunk("c2", self.GRID + timedelta(hours=16), 4, False),
        ]
        (w,) = self._windows(rows)
        assert w.state == WindowState.SKIP_UNCOMPRESSED

    def test_single_misaligned_compressed_chunk_is_rewrite(self) -> None:
        """One lone 4-hour chunk in a week still needs its slice rewritten."""
        rows = [_chunk("c1", self.GRID + timedelta(hours=12), 4, True)]
        (w,) = self._windows(rows)
        assert w.state == WindowState.REWRITE

    def test_chunks_group_into_separate_grid_windows(self) -> None:
        rows = [
            _chunk("c1", self.GRID + timedelta(hours=12), 4, True),
            _chunk("c2", self.GRID + self.INTERVAL + timedelta(hours=12), 4, True),
        ]
        w1, w2 = self._windows(rows)
        assert w1.start == self.GRID
        assert w2.start == self.GRID + self.INTERVAL
        assert w1.state == w2.state == WindowState.REWRITE


class TestCaggViewResolution:
    def test_minute_cagg_granularities_map_to_minute_views(self) -> None:
        from manta_trading.constants import GRANULARITY_SOURCE

        views = [GRANULARITY_SOURCE[g] for g in MINUTE_CAGG_GRANULARITIES]
        assert views == [
            "minute_5min_ohlcv",
            "minute_15min_ohlcv",
            "minute_hourly_ohlcv",
            "minute_4hour_ohlcv",
        ]
