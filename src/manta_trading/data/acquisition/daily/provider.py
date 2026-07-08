"""
Daily Data Provider Interface

Defines IDailyDataProvider — the daily analog of IMinuteDataProvider.

ValidationResult and RateLimitInfo are re-exported from the minute provider
module so the two providers share a single definition. Do not redefine them
here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

import pandas as pd

# Re-export shared types from the minute provider — no duplication.
from manta_trading.data.historical_minute.provider import (
    RateLimitInfo,
    ValidationResult,
)


class DailyProviderName(StrEnum):
    """Identifier for a configured daily-bar provider (slice 128).

    Mirrors ``MinuteProviderName``. Two granularities, same provider by
    default — selecting different providers per granularity is supported
    but not the recommended path.
    """

    EODHD = "eodhd"


class IDailyDataProvider(Protocol):
    """Fetch daily OHLCV bars for a single symbol from an external source.

    Returned DataFrame columns (canonical, required):
        open, high, low, close, adjusted_close, volume,
        dividend_amount, split_coefficient

    The DataFrame must be sorted ascending by date and duplicate-free.
    Raises on transport or validation errors — the orchestrator catches and
    records them.
    """

    async def fetch_daily_ohlcv(
        self,
        symbol: str,
        *,
        output_size: str,  # "compact" | "full"
    ) -> pd.DataFrame:
        """Fetch daily OHLCV data for *symbol*.

        Args:
            symbol: Stock symbol to fetch.
            output_size: "compact" (last ~100 trading days) or "full" (20+ years).

        Returns:
            DataFrame indexed by trading date with canonical columns, sorted
            ascending, duplicate-free.

        Raises:
            ValueError: If symbol or output_size is invalid.
            RuntimeError: On transport error or provider-reported failure.
        """
        ...

    def validate_response(self, raw_data: dict) -> ValidationResult:
        """Inspect a raw provider payload for rate-limit / error messages."""
        ...

    def get_rate_limits(self) -> RateLimitInfo:
        """Static rate-limit description for logging and daemon planning."""
        ...


__all__ = [
    "DailyProviderName",
    "IDailyDataProvider",
    "RateLimitInfo",
    "ValidationResult",
]
