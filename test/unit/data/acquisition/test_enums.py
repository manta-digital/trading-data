"""
Tests for Granularity and AcquisitionStatus enums.

Verifies that:
- Both are StrEnum subclasses (string equality works without .value)
- String values match what is stored in the SQL migration (770_create_acquisition_state.sql)
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from manta_trading.data.acquisition.state import AcquisitionStatus, Granularity


class TestGranularity:
    """Granularity enum — values must match the SQL migration inline comments."""

    def test_is_strenum(self):
        assert issubclass(Granularity, StrEnum)

    def test_daily_value(self):
        assert Granularity.DAILY == "daily"

    def test_minute_value(self):
        assert Granularity.MINUTE == "minute"

    def test_tick_value(self):
        assert Granularity.TICK == "tick"

    def test_string_equality(self):
        # StrEnum: enum member compares equal to its string value
        assert Granularity.DAILY == "daily"
        assert "minute" == Granularity.MINUTE

    def test_all_values(self):
        values = {g.value for g in Granularity}
        assert values == {"daily", "minute", "tick"}

    def test_constructible_from_string(self):
        assert Granularity("daily") is Granularity.DAILY
        assert Granularity("minute") is Granularity.MINUTE
        assert Granularity("tick") is Granularity.TICK

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            Granularity("weekly")


class TestAcquisitionStatus:
    """AcquisitionStatus enum — values must match the SQL migration inline comments."""

    def test_is_strenum(self):
        assert issubclass(AcquisitionStatus, StrEnum)

    def test_pending_value(self):
        assert AcquisitionStatus.PENDING == "pending"

    def test_in_progress_value(self):
        assert AcquisitionStatus.IN_PROGRESS == "in_progress"

    def test_ok_value(self):
        assert AcquisitionStatus.OK == "ok"

    def test_failed_value(self):
        assert AcquisitionStatus.FAILED == "failed"

    def test_unfillable_value(self):
        assert AcquisitionStatus.UNFILLABLE == "unfillable"

    def test_string_equality(self):
        assert AcquisitionStatus.OK == "ok"
        assert "failed" == AcquisitionStatus.FAILED

    def test_all_values(self):
        values = {s.value for s in AcquisitionStatus}
        assert values == {"pending", "in_progress", "ok", "failed", "unfillable"}

    def test_constructible_from_string(self):
        assert AcquisitionStatus("ok") is AcquisitionStatus.OK
        assert AcquisitionStatus("in_progress") is AcquisitionStatus.IN_PROGRESS

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            AcquisitionStatus("unknown")
