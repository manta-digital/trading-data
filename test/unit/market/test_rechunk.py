"""Unit tests for the rechunk driver's pure logic (slices 166, 170)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from manta_trading.constants import (
    DAILY_OHLCV_CHUNK_INTERVAL,
    MINUTE_OHLCV_CHUNK_INTERVAL,
)
from manta_trading.market.maintenance.rechunk import (
    MINUTE_CAGG_GRANULARITIES,
    RECHUNK_TARGETS,
    PreflightError,
    RechunkTarget,
    WindowState,
    _assert_dimension_interval,
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
        week = timedelta(days=7)
        assert _window_start(_utc(2025, 1, 6, 15, 30), week) == _utc(2025, 1, 2)

    def test_window_end_minus_epsilon_stays_in_window(self) -> None:
        week = timedelta(days=7)
        end = _utc(2025, 1, 9)
        assert _window_start(end - timedelta(seconds=1), week) == _utc(2025, 1, 2)
        assert _window_start(end, week) == _utc(2025, 1, 9)


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


class TestTargetRegistry:
    """The registry is the single place a table-specific value is stated
    (slice 170 D2); dispatch is by enum, never by string comparison."""

    def test_registry_covers_every_enum_member(self) -> None:
        """Guards against adding a RechunkTarget without a spec — a KeyError
        at run time on production would otherwise be the first symptom."""
        assert set(RECHUNK_TARGETS) == set(RechunkTarget)

    def test_minute_target_spec(self) -> None:
        spec = RECHUNK_TARGETS[RechunkTarget.MINUTE]
        assert spec.table == "minute_ohlcv"
        assert spec.interval == MINUTE_OHLCV_CHUNK_INTERVAL
        assert spec.cagg_views == (
            "minute_5min_ohlcv",
            "minute_15min_ohlcv",
            "minute_hourly_ohlcv",
            "minute_4hour_ohlcv",
        )

    def test_daily_target_spec(self) -> None:
        spec = RECHUNK_TARGETS[RechunkTarget.DAILY]
        assert spec.table == "daily_ohlcv"
        assert spec.interval == DAILY_OHLCV_CHUNK_INTERVAL
        # daily_coverage also materializes from daily_ohlcv, so its refresh
        # policy carries the same mid-rewrite hazard as the three rollups.
        assert spec.cagg_views == (
            "daily_weekly_ohlcv",
            "daily_monthly_ohlcv",
            "daily_quarterly_ohlcv",
            "daily_coverage",
        )

    def test_daily_interval_nests_the_pre_170_interval(self) -> None:
        """70 = 10 x 7: the property that makes every target window contain
        only whole 7-day chunks, satisfying 166's grid-alignment caveat."""
        assert DAILY_OHLCV_CHUNK_INTERVAL % timedelta(days=7) == timedelta(0)


def _mock_conn_with_dimension(interval: timedelta | None) -> MagicMock:
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = None if interval is None else (interval,)
    return conn


class TestPreflightMigrationId:
    """A pre-flight failure must name the migration that fixes it — the
    operator's next action is 'apply that migration', so naming the wrong one
    sends them to a no-op."""

    @pytest.mark.parametrize(
        ("target", "expected_id"),
        [
            (RechunkTarget.MINUTE, "043"),
            (RechunkTarget.DAILY, "050"),
        ],
    )
    def test_wrong_interval_names_the_targets_migration(
        self, target: RechunkTarget, expected_id: str
    ) -> None:
        spec = RECHUNK_TARGETS[target]
        conn = _mock_conn_with_dimension(timedelta(hours=4))
        with pytest.raises(PreflightError) as exc:
            _assert_dimension_interval(
                conn, spec.table, spec.interval, spec.interval_migration_id
            )
        assert expected_id in str(exc.value)

    def test_matching_interval_passes(self) -> None:
        spec = RECHUNK_TARGETS[RechunkTarget.DAILY]
        conn = _mock_conn_with_dimension(spec.interval)
        _assert_dimension_interval(
            conn, spec.table, spec.interval, spec.interval_migration_id
        )

    def test_missing_hypertable_is_a_preflight_error(self) -> None:
        spec = RECHUNK_TARGETS[RechunkTarget.DAILY]
        conn = _mock_conn_with_dimension(None)
        with pytest.raises(PreflightError, match="not a hypertable"):
            _assert_dimension_interval(
                conn, spec.table, spec.interval, spec.interval_migration_id
            )


class TestDailyGridNesting:
    """B3.2 — 7-day chunks group onto the 70-day grid with no window split."""

    INTERVAL = DAILY_OHLCV_CHUNK_INTERVAL  # 70 days
    # 2024-10-24 is 20020 days after the epoch; 20020 % 70 == 0. Its window
    # ends 2025-01-02, which TestWindowStart independently pins as a 7-day
    # boundary — the nesting property in concrete dates.
    GRID = _utc(2024, 10, 24)

    def _windows(self, rows: list[dict]):
        return _load_windows(_mock_conn_with_chunks(rows), "t", self.INTERVAL)

    def test_grid_constant_is_actually_on_the_grid(self) -> None:
        assert _window_start(self.GRID, self.INTERVAL) == self.GRID

    def test_ten_seven_day_chunks_group_into_exactly_one_window(self) -> None:
        """The nesting property, asserted end to end: a full 70-day span of
        7-day chunks yields one window, not two partially-filled ones."""
        rows = [
            _chunk(f"c{i}", self.GRID + timedelta(days=7 * i), 7 * 24, True)
            for i in range(10)
        ]
        (w,) = self._windows(rows)
        assert w.state == WindowState.REWRITE
        assert w.start == self.GRID
        assert w.end == self.GRID + self.INTERVAL
        assert len(w.chunks) == 10

    def test_last_seven_day_chunk_ends_exactly_on_the_window_boundary(self) -> None:
        """No 7-day chunk straddles a 70-day boundary — the whole-chunks
        guarantee the rewrite depends on."""
        last_start = self.GRID + timedelta(days=63)
        assert _window_start(last_start, self.INTERVAL) == self.GRID
        assert last_start + timedelta(days=7) == self.GRID + self.INTERVAL

    def test_next_seven_day_chunk_starts_the_next_window(self) -> None:
        rows = [
            _chunk("c1", self.GRID + timedelta(days=63), 7 * 24, True),
            _chunk("c2", self.GRID + timedelta(days=70), 7 * 24, True),
        ]
        w1, w2 = self._windows(rows)
        assert w1.start == self.GRID
        assert w2.start == self.GRID + self.INTERVAL

    def test_single_aligned_seventy_day_chunk_is_done(self) -> None:
        """Post-rechunk steady state: a re-run is a no-op."""
        rows = [_chunk("c1", self.GRID, 70 * 24, True)]
        (w,) = self._windows(rows)
        assert w.state == WindowState.DONE
