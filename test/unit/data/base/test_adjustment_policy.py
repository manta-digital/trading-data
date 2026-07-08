"""
Unit tests for adjustment_policy module.

Tests enums, dataclasses, and OHLCV validation functions.
"""

import pytest
from datetime import datetime, timezone
from manta_trading.data.base.adjustment_policy import (
    AdjustmentPolicy,
    SessionType,
    DataVersion,
    ValidationResult,
    validate_ohlcv_consistency
)


class TestAdjustmentPolicy:
    """Tests for AdjustmentPolicy enum."""

    def test_enum_values(self):
        """Test that enum values are correct."""
        assert AdjustmentPolicy.SPLIT_ADJUSTED.value == "split_adjusted"
        assert AdjustmentPolicy.RAW.value == "raw"
        assert AdjustmentPolicy.DIVIDEND_ADJUSTED.value == "dividend_adjusted"

    def test_string_representation(self):
        """Test string representation of enum values."""
        assert str(AdjustmentPolicy.SPLIT_ADJUSTED) == "split_adjusted"
        assert str(AdjustmentPolicy.RAW) == "raw"
        assert str(AdjustmentPolicy.DIVIDEND_ADJUSTED) == "dividend_adjusted"


class TestSessionType:
    """Tests for SessionType enum."""

    def test_enum_values(self):
        """Test that enum values are correct."""
        assert SessionType.RTH.value == "RTH"
        assert SessionType.ETH.value == "ETH"
        assert SessionType.ALL.value == "ALL"

    def test_string_representation(self):
        """Test string representation of enum values."""
        assert str(SessionType.RTH) == "RTH"
        assert str(SessionType.ETH) == "ETH"
        assert str(SessionType.ALL) == "ALL"


class TestDataVersion:
    """Tests for DataVersion dataclass."""

    def test_instantiation(self):
        """Test creating a DataVersion instance."""
        now = datetime.now(timezone.utc)
        dv = DataVersion(
            version="1.0.0",
            ingestion_timestamp=now,
            provider_version="av_2024"
        )
        assert dv.version == "1.0.0"
        assert dv.ingestion_timestamp == now
        assert dv.provider_version == "av_2024"

    def test_without_provider_version(self):
        """Test creating DataVersion without provider_version."""
        now = datetime.now(timezone.utc)
        dv = DataVersion(version="1.0.0", ingestion_timestamp=now)
        assert dv.version == "1.0.0"
        assert dv.ingestion_timestamp == now
        assert dv.provider_version is None

    def test_timezone_aware_required(self):
        """Test that timezone-aware timestamp is required."""
        naive_dt = datetime.now()  # No timezone
        with pytest.raises(ValueError, match="must be timezone-aware"):
            DataVersion(version="1.0.0", ingestion_timestamp=naive_dt)


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_instantiation(self):
        """Test creating a ValidationResult instance."""
        vr = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=["suspicious pattern"]
        )
        assert vr.is_valid is True
        assert vr.errors == []
        assert vr.warnings == ["suspicious pattern"]

    def test_warnings_default(self):
        """Test that warnings defaults to empty list."""
        vr = ValidationResult(is_valid=False, errors=["error1"])
        assert vr.warnings == []


class TestValidateOHLCVConsistency:
    """Tests for validate_ohlcv_consistency function."""

    def test_valid_ohlcv(self):
        """Test validation passes for valid OHLCV data."""
        result = validate_ohlcv_consistency(
            open_price=100.0,
            high=105.0,
            low=99.0,
            close=103.0
        )
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_high_less_than_close_fails(self):
        """Test validation fails when high < close."""
        result = validate_ohlcv_consistency(
            open_price=100.0,
            high=102.0,
            low=99.0,
            close=105.0  # close > high
        )
        assert result.is_valid is False
        assert any("high" in err and "max(open, close)" in err for err in result.errors)

    def test_high_less_than_open_fails(self):
        """Test validation fails when high < open."""
        result = validate_ohlcv_consistency(
            open_price=105.0,  # open > high
            high=102.0,
            low=99.0,
            close=100.0
        )
        assert result.is_valid is False
        assert any("high" in err and "max(open, close)" in err for err in result.errors)

    def test_low_greater_than_open_fails(self):
        """Test validation fails when low > open."""
        result = validate_ohlcv_consistency(
            open_price=100.0,
            high=105.0,
            low=101.0,  # low > open
            close=103.0
        )
        assert result.is_valid is False
        assert any("low" in err and "min(open, close)" in err for err in result.errors)

    def test_low_greater_than_close_fails(self):
        """Test validation fails when low > close."""
        result = validate_ohlcv_consistency(
            open_price=105.0,
            high=106.0,
            low=104.0,  # low > close
            close=103.0
        )
        assert result.is_valid is False
        assert any("low" in err and "min(open, close)" in err for err in result.errors)

    def test_negative_prices_fail_by_default(self):
        """Test that negative prices fail validation by default."""
        result = validate_ohlcv_consistency(
            open_price=-100.0,
            high=-99.0,
            low=-101.0,
            close=-100.0
        )
        assert result.is_valid is False
        assert any("negative" in err.lower() for err in result.errors)

    def test_negative_prices_allowed_when_flag_set(self):
        """Test that negative prices pass when allow_negative=True."""
        result = validate_ohlcv_consistency(
            open_price=-100.0,
            high=-99.0,
            low=-101.0,
            close=-100.0,
            allow_negative=True
        )
        # Should pass consistency checks even with negative values
        assert result.is_valid is True

    def test_nan_value_fails(self):
        """Test that NaN values fail validation."""
        result = validate_ohlcv_consistency(
            open_price=float('nan'),
            high=105.0,
            low=99.0,
            close=103.0
        )
        assert result.is_valid is False
        assert any("NaN" in err for err in result.errors)

    def test_infinite_value_fails(self):
        """Test that infinite values fail validation."""
        result = validate_ohlcv_consistency(
            open_price=100.0,
            high=float('inf'),
            low=99.0,
            close=103.0
        )
        assert result.is_valid is False
        assert any("infinite" in err for err in result.errors)

    def test_flat_bar_warning(self):
        """Test that flat bars (all prices equal) generate warnings."""
        result = validate_ohlcv_consistency(
            open_price=100.0,
            high=100.0,
            low=100.0,
            close=100.0
        )
        assert result.is_valid is True  # Valid but suspicious
        assert len(result.warnings) > 0
        assert any("identical" in warn.lower() for warn in result.warnings)

    def test_invalid_type_fails(self):
        """Test that non-numeric types fail validation."""
        result = validate_ohlcv_consistency(
            open_price="100",  # String instead of number
            high=105.0,
            low=99.0,
            close=103.0
        )
        assert result.is_valid is False
        assert any("must be a number" in err for err in result.errors)
