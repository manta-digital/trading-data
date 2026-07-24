"""Unit tests for the slice 163 cagg parity core (pure logic + boundaries).

Covers the three D1 crash-window parity outcomes (DONE / empty-PENDING /
partial-PENDING), epoch-grid alignment (a straddling range yields two windows),
per-year rollup arithmetic using the design's measured baseline, and
granularity filtering. The DB is mocked at the query boundary; the arithmetic
is exercised with real numbers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from manta_trading.constants import MINUTE_CAGG_CHUNK_INTERVAL, Granularity
from manta_trading.market.maintenance.cagg_parity import (
    CaggChunkSummary,
    WindowCounts,
    WindowParity,
    YearParity,
    _epoch_grid_windows,
    compute_parity,
    rollup_by_year,
)

_70D = MINUTE_CAGG_CHUNK_INTERVAL


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Epoch-grid window enumeration
# ---------------------------------------------------------------------------


class TestEpochGridWindows:
    def test_single_window_covers_range_inside_one_grid_cell(self) -> None:
        # Take a real window's interior so lo/hi cannot straddle a grid line.
        cell_start = _epoch_grid_windows(_utc(2020, 1, 1), _utc(2020, 1, 1), _70D)[0][0]
        lo = cell_start + timedelta(days=1)
        hi = cell_start + timedelta(days=10)
        windows = _epoch_grid_windows(lo, hi, _70D)
        assert len(windows) == 1
        # Window is grid-aligned: start ≤ lo, and end = start + 70d.
        start, end = windows[0]
        assert start == cell_start
        assert start <= lo
        assert end == start + _70D

    def test_straddling_range_yields_two_windows(self) -> None:
        # Pick a range that crosses exactly one 70-day grid line: start in one
        # window, end in the next. The grid line at start-of-window boundary
        # splits it into two.
        first_start = _epoch_grid_windows(
            _utc(2020, 1, 1), _utc(2020, 1, 1), _70D
        )[0][0]
        grid_line = first_start + _70D
        lo = grid_line - timedelta(days=1)
        hi = grid_line + timedelta(days=1)
        windows = _epoch_grid_windows(lo, hi, _70D)
        assert len(windows) == 2
        # Contiguous and grid-aligned.
        assert windows[0][1] == windows[1][0]
        assert windows[1][0] == grid_line

    def test_windows_are_contiguous_and_ascending(self) -> None:
        windows = _epoch_grid_windows(_utc(2019, 1, 1), _utc(2021, 6, 1), _70D)
        assert len(windows) > 1
        for prev, nxt in zip(windows, windows[1:], strict=False):
            assert prev[1] == nxt[0]
            assert prev[0] < nxt[0]
            assert nxt[1] - nxt[0] == _70D


# ---------------------------------------------------------------------------
# Parity state derivation — the three D1 crash-window outcomes
# ---------------------------------------------------------------------------


class TestWindowParityState:
    def test_equal_counts_is_done(self) -> None:
        w = WindowCounts(_utc(2020, 1, 1), _utc(2020, 3, 11), 1000, 1000)
        assert w.parity is WindowParity.DONE
        assert w.coverage == 1.0

    def test_empty_cagg_vs_nonzero_raw_is_pending(self) -> None:
        # Kill-after-drop crash window: cagg region empty, raw non-zero.
        w = WindowCounts(_utc(2020, 1, 1), _utc(2020, 3, 11), 1000, 0)
        assert w.parity is WindowParity.PENDING
        assert w.coverage == 0.0

    def test_partial_materialization_is_pending(self) -> None:
        # The ~21% under-materialization signature.
        w = WindowCounts(_utc(2020, 1, 1), _utc(2020, 3, 11), 1000, 208)
        assert w.parity is WindowParity.PENDING
        assert 0.20 < w.coverage < 0.21

    def test_zero_raw_zero_cagg_is_done(self) -> None:
        # A window with no raw rows (market-hours gap edge) is trivially at parity.
        w = WindowCounts(_utc(2020, 1, 1), _utc(2020, 3, 11), 0, 0)
        assert w.parity is WindowParity.DONE
        assert w.coverage == 0.0


# ---------------------------------------------------------------------------
# Per-year rollup — using the design's measured 2019 baseline
# ---------------------------------------------------------------------------


class TestRollupByYear:
    def test_rollup_sums_windows_within_year(self) -> None:
        windows = [
            WindowCounts(_utc(2019, 1, 1), _utc(2019, 3, 12), 100_000_000, 20_800_000),
            WindowCounts(_utc(2019, 4, 1), _utc(2019, 6, 10), 108_673_609, 22_640_140),
        ]
        years = rollup_by_year(windows)
        assert len(years) == 1
        y = years[0]
        assert y.year == 2019
        # Matches the design baseline: 2019 → 208,673,609 raw, 43,440,140 cagg.
        assert y.raw_count == 208_673_609
        assert y.cagg_count == 43_440_140
        assert y.parity is WindowParity.PENDING
        assert 0.20 < y.coverage < 0.21  # ≈ 20.8%

    def test_rollup_splits_by_year_ascending(self) -> None:
        windows = [
            WindowCounts(_utc(2021, 1, 1), _utc(2021, 3, 12), 280_079_556, 46_267_456),
            WindowCounts(_utc(2019, 1, 1), _utc(2019, 3, 12), 208_673_609, 43_440_140),
        ]
        years = rollup_by_year(windows)
        assert [y.year for y in years] == [2019, 2021]

    def test_full_parity_year_is_done(self) -> None:
        windows = [
            WindowCounts(_utc(2020, 1, 1), _utc(2020, 3, 11), 500, 500),
        ]
        years = rollup_by_year(windows)
        assert years[0].parity is WindowParity.DONE


# ---------------------------------------------------------------------------
# compute_parity — granularity filtering, boundary mocking
# ---------------------------------------------------------------------------


class TestComputeParityBoundary:
    """compute_parity drives the query helpers; here we stub them to verify
    it maps granularities → reports and threads the window list through."""

    def _patch_helpers(self, *, bounds, window_counts, chunk_summary):
        # Patch the four DB-touching helpers + the connection wrapper so no real
        # connection is opened.
        mod = "manta_trading.market.maintenance.cagg_parity"
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=MagicMock())
        cm.__exit__ = MagicMock(return_value=False)
        return (
            patch(f"{mod}._TimeoutConnection", return_value=cm),
            patch(f"{mod}._raw_bounds", return_value=bounds),
            patch(f"{mod}._window_counts", return_value=window_counts),
            patch(f"{mod}._chunk_summary", return_value=chunk_summary),
        )

    def test_returns_one_report_per_requested_granularity(self) -> None:
        wc = [WindowCounts(_utc(2019, 1, 1), _utc(2019, 3, 12), 100, 21)]
        cs = CaggChunkSummary("v", 117, _70D)
        p1, p2, p3, p4 = self._patch_helpers(
            bounds=(_utc(2019, 1, 1), _utc(2019, 2, 1)),
            window_counts=wc,
            chunk_summary=cs,
        )
        with p1, p2, p3, p4:
            reports = compute_parity(
                "postgresql://x", (Granularity.H4, Granularity.H1)
            )
        assert [r.granularity for r in reports] == [Granularity.H4, Granularity.H1]
        assert reports[0].view_name == "minute_4hour_ohlcv"
        assert reports[1].view_name == "minute_hourly_ohlcv"

    def test_report_aggregates_totals_and_parity(self) -> None:
        wc = [
            WindowCounts(_utc(2019, 1, 1), _utc(2019, 3, 12), 100, 21),
            WindowCounts(_utc(2019, 3, 12), _utc(2019, 5, 21), 200, 200),
        ]
        cs = CaggChunkSummary("minute_4hour_ohlcv", 117, _70D)
        p1, p2, p3, p4 = self._patch_helpers(
            bounds=(_utc(2019, 1, 1), _utc(2019, 6, 1)),
            window_counts=wc,
            chunk_summary=cs,
        )
        with p1, p2, p3, p4:
            reports = compute_parity("postgresql://x", (Granularity.H4,))
        r = reports[0]
        assert r.raw_total == 300
        assert r.cagg_total == 221
        assert r.in_parity is False  # one window PENDING
        assert r.chunk_summary.chunk_count == 117

    def test_empty_raw_table_yields_empty_windows(self) -> None:
        cs = CaggChunkSummary("minute_4hour_ohlcv", 0, None)
        p1, p2, p3, p4 = self._patch_helpers(
            bounds=None, window_counts=[], chunk_summary=cs
        )
        with p1, p2, p3, p4:
            reports = compute_parity("postgresql://x", (Granularity.H4,))
        r = reports[0]
        assert r.windows == []
        assert r.years == []
        assert r.in_parity is True  # vacuously — no window is PENDING


class TestYearParityHelper:
    def test_coverage_zero_when_raw_empty(self) -> None:
        y = YearParity(2020, 0, 0)
        assert y.coverage == 0.0
        assert y.parity is WindowParity.DONE
