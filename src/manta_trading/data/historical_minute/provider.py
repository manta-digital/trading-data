"""
Minute Data Provider Interface Module

This module defines the provider abstraction pattern for historical minute data acquisition.
Providers encapsulate data source-specific logic (API calls, authentication, rate limiting,
format conversion) behind a common interface.

The provider abstraction enables:
- Easy switching between data providers (EODHD, Databento, Polygon, etc.)
- Provider-specific logic isolation
- Simplified testing with mock providers
- Configuration-driven provider selection

Standard DataFrame Schema:
    All providers must convert their data to this standard format:
    - timestamp (datetime): Bar timestamp
    - open (float): Opening price
    - high (float): High price
    - low (float): Low price
    - close (float): Closing price
    - volume (int): Trading volume

Example usage:
    ```python
    from manta_trading.data.historical_minute.provider import IMinuteDataProvider
    from manta_trading.data.historical_minute.providers.eodhd import EODHDMinuteProvider

    # Initialize provider
    provider = EODHDMinuteProvider(api_key="YOUR_KEY")

    # Fetch data
    raw_response = await provider.fetch_minute_data('AAPL', start_date, end_date)

    # Validate response
    validation = provider.validate_response(raw_response.raw_data)
    if not validation.is_valid:
        print(f"Errors: {validation.errors}")

    # Convert to standard format
    df = provider.convert_to_standard_format(raw_response)
    ```
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

import pandas as pd


class MinuteProviderName(StrEnum):
    """Identifier for a configured minute-bar provider.

    Co-located with the protocol because the configuration layer needs to
    reference these names without pulling in any concrete provider
    implementation. The ``build_minute_provider`` helper that constructs
    instances lives in ``data.historical_minute.providers.__init__``.
    """

    EODHD = "eodhd"


@dataclass
class RawDataResponse:
    """
    Raw data response from a provider.

    Attributes:
        symbol: Stock symbol requested
        provider: Name of the data provider (e.g., 'alphavantage', 'polygon')
        start_date: Start of requested date range
        end_date: End of requested date range
        raw_data: Provider-specific raw data (typically dict from JSON response)
        metadata: Additional metadata about the fetch (timestamps, pagination info, etc.)
    """
    symbol: str
    provider: str
    start_date: datetime
    end_date: datetime
    raw_data: dict[str, Any]
    metadata: dict[str, Any]


@dataclass
class RateLimitInfo:
    """
    Rate limit information for a provider.

    Attributes:
        requests_per_minute: Maximum requests allowed per minute
        requests_per_day: Maximum requests allowed per day (None if unlimited)
        current_usage: Current number of requests used in current period
        reset_time: Time when rate limit counter resets (None if not applicable)
    """
    requests_per_minute: int
    requests_per_day: int | None
    current_usage: int
    reset_time: datetime | None

    def __post_init__(self):
        """Validate rate limit values."""
        if self.requests_per_minute < 0:
            raise ValueError(f"Invalid requests_per_minute: {self.requests_per_minute}. Must be non-negative")
        if self.requests_per_day is not None and self.requests_per_day < 0:
            raise ValueError(f"Invalid requests_per_day: {self.requests_per_day}. Must be non-negative or None")
        if self.current_usage < 0:
            raise ValueError(f"Invalid current_usage: {self.current_usage}. Must be non-negative")


@dataclass
class ValidationResult:
    """
    Result of validating a provider response.

    Attributes:
        is_valid: True if response is valid and usable
        errors: List of error messages (empty if valid)
        warnings: List of warning messages (non-fatal issues)
    """
    is_valid: bool
    errors: list[str]
    warnings: list[str]


class IMinuteDataProvider(Protocol):
    """
    Protocol defining the interface for minute data providers.

    All minute data providers must implement this interface to ensure compatibility
    with minute data acquisition. Using Protocol enables duck typing - any class
    implementing these methods will be considered compatible.

    Provider Responsibilities:
    - Authenticate with data source
    - Handle provider-specific API calls
    - Manage rate limiting
    - Convert provider-specific formats to standard DataFrame schema
    - Validate responses for errors
    - Declare its single-request chunk window via ``max_days_per_request``
    """

    max_days_per_request: int
    """Maximum number of calendar days that can be requested in a single
    ``fetch_minute_data`` call. The orchestrator chunks longer ranges into
    multiple calls of at most this many days each. Each provider declares
    the value documented by its API (e.g. EODHD = 120, AlphaVantage = 30)."""

    async def fetch_minute_data(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> RawDataResponse:
        """
        Fetch raw minute data from the provider.

        This method is async to support I/O-bound operations (HTTP requests).
        Implementations should handle:
        - Provider-specific pagination
        - Rate limiting
        - Authentication
        - Network errors and retries

        Args:
            symbol: Stock symbol to fetch
            start_date: Start of date range
            end_date: End of date range

        Returns:
            RawDataResponse containing provider-specific raw data

        Raises:
            ValueError: If symbol is invalid
            RuntimeError: If API request fails
            TimeoutError: If request times out

        Example:
            ```python
            response = await provider.fetch_minute_data('AAPL', start, end)
            print(f"Fetched {len(response.raw_data)} records")
            ```
        """
        ...

    def get_rate_limits(self) -> RateLimitInfo:
        """
        Get rate limit information for this provider.

        Returns:
            RateLimitInfo with current rate limit settings and usage

        Example:
            ```python
            limits = provider.get_rate_limits()
            print(f"Requests per minute: {limits.requests_per_minute}")
            print(f"Current usage: {limits.current_usage}")
            ```
        """
        ...

    def validate_response(self, raw_data: dict[str, Any]) -> ValidationResult:
        """
        Validate a raw provider response for errors and data quality.

        This should check for:
        - Provider-specific error messages
        - Empty or missing data
        - Malformed JSON structure
        - API rate limit messages

        Args:
            raw_data: Raw data dictionary from provider

        Returns:
            ValidationResult indicating if response is valid and any errors/warnings

        Example:
            ```python
            validation = provider.validate_response(response.raw_data)
            if not validation.is_valid:
                for error in validation.errors:
                    print(f"Error: {error}")
            ```
        """
        ...

    def convert_to_standard_format(self, raw_data: RawDataResponse) -> pd.DataFrame:
        """
        Convert provider-specific raw data to standard DataFrame format.

        The returned DataFrame must have these columns:
        - timestamp (datetime): Bar timestamp
        - open (float): Opening price
        - high (float): High price
        - low (float): Low price
        - close (float): Closing price
        - volume (int): Trading volume

        The DataFrame should be:
        - Sorted by timestamp ascending
        - Free of duplicates
        - Have proper data types

        Args:
            raw_data: RawDataResponse from fetch_minute_data()

        Returns:
            DataFrame with standard OHLCV schema

        Raises:
            ValueError: If raw_data cannot be converted
            KeyError: If required fields are missing

        Example:
            ```python
            df = provider.convert_to_standard_format(response)
            assert list(df.columns) == ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            assert df['timestamp'].is_monotonic_increasing
            ```
        """
        ...


__all__ = [
    'IMinuteDataProvider',
    'MinuteProviderName',
    'RawDataResponse',
    'RateLimitInfo',
    'ValidationResult',
]
