"""Unit tests: AGGREGATION_VIEWS correctness after slice 152."""

from __future__ import annotations

from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB


class TestAggregationViews:
    def test_no_v2_suffixes(self) -> None:
        for key, view in TimescaleMinuteDataDB.AGGREGATION_VIEWS.items():
            assert "_v2" not in view, (
                f"AGGREGATION_VIEWS[{key!r}] = {view!r} still contains _v2 suffix"
            )

    def test_expected_keys(self) -> None:
        expected = {"5m", "15m", "1h", "4h"}
        assert set(TimescaleMinuteDataDB.AGGREGATION_VIEWS.keys()) == expected

    def test_view_names_match_new_caggs(self) -> None:
        expected_views = {
            "minute_5min_ohlcv",
            "minute_15min_ohlcv",
            "minute_hourly_ohlcv",
            "minute_4hour_ohlcv",
        }
        assert set(TimescaleMinuteDataDB.AGGREGATION_VIEWS.values()) == expected_views

    def test_no_daily_weekly_monthly_in_minute_views(self) -> None:
        """Daily/weekly/monthly rollups come from daily_ohlcv caggs, not minute."""
        for view in TimescaleMinuteDataDB.AGGREGATION_VIEWS.values():
            assert "daily" not in view
            assert "weekly" not in view
            assert "monthly" not in view
