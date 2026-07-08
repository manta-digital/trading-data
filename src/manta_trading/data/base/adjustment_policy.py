"""
Data adjustment policies and validation utilities.

This module defines enums and dataclasses for tracking data adjustment policies,
session types, data versioning, and OHLCV validation logic.
"""

from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class AdjustmentPolicy(Enum):
    """
    Defines how price data should be adjusted for corporate actions.

    SPLIT_ADJUSTED: Prices adjusted for stock splits and reverse splits
    RAW: Unadjusted prices as originally reported
    DIVIDEND_ADJUSTED: Prices adjusted for both splits and dividends
    """
    SPLIT_ADJUSTED = "split_adjusted"
    RAW = "raw"
    DIVIDEND_ADJUSTED = "dividend_adjusted"

    def __str__(self) -> str:
        return self.value


class SessionType(Enum):
    """
    Defines trading session types for session partitioning.

    RTH: Regular Trading Hours (9:30 AM - 4:00 PM ET for US equities)
    ETH: Extended Trading Hours (pre-market and after-hours)
    ALL: All trading hours (both RTH and ETH combined)
    """
    RTH = "RTH"
    ETH = "ETH"
    ALL = "ALL"

    def __str__(self) -> str:
        return self.value


@dataclass
class DataVersion:
    """
    Tracks data versioning and ingestion metadata.

    Attributes:
        version: Semantic version of the data (e.g., "1.0.0")
        ingestion_timestamp: When the data was ingested into the database
        provider_version: Version identifier from the data provider
    """
    version: str
    ingestion_timestamp: datetime
    provider_version: Optional[str] = None

    def __post_init__(self):
        """Validate that ingestion_timestamp is timezone-aware."""
        if self.ingestion_timestamp.tzinfo is None:
            raise ValueError("ingestion_timestamp must be timezone-aware")


@dataclass
class ValidationResult:
    """
    Result of OHLCV data validation.

    Attributes:
        is_valid: True if all validation checks passed
        errors: List of error messages for failed validations
        warnings: List of warning messages for suspicious but not invalid data
    """
    is_valid: bool
    errors: list[str]
    warnings: list[str] = None

    def __post_init__(self):
        """Initialize warnings list if not provided."""
        if self.warnings is None:
            self.warnings = []


def validate_ohlcv_consistency(
    open_price: float,
    high: float,
    low: float,
    close: float,
    allow_negative: bool = False
) -> ValidationResult:
    """
    Validate OHLCV data for internal consistency.

    Checks:
    1. High >= max(open, close)
    2. Low <= min(open, close)
    3. No negative prices (if allow_negative is False)
    4. All prices are finite numbers

    Args:
        open_price: Opening price
        high: High price
        low: Low price
        close: Closing price
        allow_negative: If False, negative prices are errors (default: False)

    Returns:
        ValidationResult with is_valid flag and any error messages
    """
    errors = []
    warnings = []

    # Check for finite numbers
    prices = [open_price, high, low, close]
    price_names = ['open', 'high', 'low', 'close']

    for price, name in zip(prices, price_names):
        if not isinstance(price, (int, float)):
            errors.append(f"{name} must be a number, got {type(price).__name__}")
        elif not (price == price):  # NaN check
            errors.append(f"{name} is NaN")
        elif price == float('inf') or price == float('-inf'):
            errors.append(f"{name} is infinite")

    # If we have type errors, don't proceed with value checks
    if errors:
        return ValidationResult(is_valid=False, errors=errors, warnings=warnings)

    # Check for negative prices
    if not allow_negative:
        for price, name in zip(prices, price_names):
            if price < 0:
                errors.append(f"{name} cannot be negative: {price}")

    # Check high >= max(open, close)
    max_oc = max(open_price, close)
    if high < max_oc:
        errors.append(f"high ({high}) must be >= max(open, close) ({max_oc})")

    # Check low <= min(open, close)
    min_oc = min(open_price, close)
    if low > min_oc:
        errors.append(f"low ({low}) must be <= min(open, close) ({min_oc})")

    # Warnings for suspicious (but not invalid) data
    if high == low and high == open_price == close:
        warnings.append("All OHLC prices are identical (possible flat bar)")

    is_valid = len(errors) == 0
    return ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)
