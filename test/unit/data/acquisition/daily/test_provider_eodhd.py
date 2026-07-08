"""Unit tests for EODHDDailyProvider (slice 128).

The HTTP path is exercised by stubbing
``manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry``
so no network call is made.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from manta_trading.data.acquisition.daily.providers.eodhd import (
    EODHDDailyProvider,
)
from manta_trading.providers.errors import ProviderPermanentError


_FIXTURE = [
    {
        "date": "2024-01-02",
        "open": 187.15,
        "high": 188.44,
        "low": 183.89,
        "close": 185.64,
        "adjusted_close": 184.78,
        "volume": 82488700,
    },
    {
        "date": "2024-01-03",
        "open": 184.22,
        "high": 185.88,
        "low": 183.43,
        "close": 184.25,
        "adjusted_close": 183.39,
        "volume": 58414500,
    },
]


class TestFetchHappyPath:
    def test_returns_canonical_columns_sorted(self):
        provider = EODHDDailyProvider(api_key="k")
        with patch(
            "manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=_FIXTURE),
        ):
            df = asyncio.run(provider.fetch_daily_ohlcv("AAPL"))
        # Canonical columns present in the right order
        assert list(df.columns) == [
            "open", "high", "low", "close", "adjusted_close",
            "volume", "dividend_amount", "split_coefficient",
        ]
        # Sorted ascending
        assert df.index.is_monotonic_increasing
        # Per-row CA columns are 0.0 (slice 128: CA tables hold the truth)
        assert (df["dividend_amount"] == 0.0).all()
        assert (df["split_coefficient"] == 0.0).all()
        # Row count matches fixture
        assert len(df) == 2

    def test_normalises_bare_symbol(self):
        provider = EODHDDailyProvider(api_key="k")
        captured = []

        async def _stub(*, client, url, api_key, logger, timeout, max_retries=3):
            captured.append(url)
            return []

        with patch(
            "manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry",
            new=_stub,
        ):
            asyncio.run(provider.fetch_daily_ohlcv("AAPL"))
        assert "/eod/AAPL.US" in captured[0]

    def test_compact_adds_from_param(self):
        provider = EODHDDailyProvider(api_key="k")
        captured = []

        async def _stub(*, client, url, api_key, logger, timeout, max_retries=3):
            captured.append(url)
            return []

        with patch(
            "manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry",
            new=_stub,
        ):
            asyncio.run(provider.fetch_daily_ohlcv("AAPL", output_size="compact"))
        assert "&from=" in captured[0]

    def test_full_omits_from_param(self):
        provider = EODHDDailyProvider(api_key="k")
        captured = []

        async def _stub(*, client, url, api_key, logger, timeout, max_retries=3):
            captured.append(url)
            return []

        with patch(
            "manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry",
            new=_stub,
        ):
            asyncio.run(provider.fetch_daily_ohlcv("AAPL", output_size="full"))
        assert "&from=" not in captured[0]


class TestFetchEdgeCases:
    def test_empty_payload_returns_empty_canonical_frame(self):
        provider = EODHDDailyProvider(api_key="k")
        with patch(
            "manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            df = asyncio.run(provider.fetch_daily_ohlcv("UNKN"))
        assert df.empty
        assert list(df.columns) == [
            "open", "high", "low", "close", "adjusted_close",
            "volume", "dividend_amount", "split_coefficient",
        ]

    def test_non_list_payload_raises_permanent(self):
        provider = EODHDDailyProvider(api_key="k")
        with patch(
            "manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value={"error": "boom"}),
        ):
            with pytest.raises(ProviderPermanentError, match="unexpected /eod"):
                asyncio.run(provider.fetch_daily_ohlcv("AAPL"))

    def test_missing_date_field_raises_permanent(self):
        provider = EODHDDailyProvider(api_key="k")
        with patch(
            "manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=[{"open": 1, "close": 1}]),
        ):
            with pytest.raises(ProviderPermanentError, match="missing 'date'"):
                asyncio.run(provider.fetch_daily_ohlcv("AAPL"))

    def test_empty_symbol_rejected(self):
        provider = EODHDDailyProvider(api_key="k")
        with pytest.raises(ValueError, match="non-empty"):
            asyncio.run(provider.fetch_daily_ohlcv(""))

    def test_invalid_output_size_rejected(self):
        provider = EODHDDailyProvider(api_key="k")
        with pytest.raises(ValueError, match="compact"):
            asyncio.run(provider.fetch_daily_ohlcv("AAPL", output_size="huge"))

    def test_null_volume_coerced_to_zero(self):
        provider = EODHDDailyProvider(api_key="k")
        fixture = [
            {
                "date": "2024-01-02",
                "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                "adjusted_close": 1.0, "volume": None,
            },
        ]
        with patch(
            "manta_trading.data.acquisition.daily.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=fixture),
        ):
            df = asyncio.run(provider.fetch_daily_ohlcv("AAPL"))
        assert df["volume"].iloc[0] == 0.0


class TestApiKeyRequired:
    def test_empty_key_rejected(self):
        with pytest.raises(ValueError, match="api_key"):
            EODHDDailyProvider(api_key="")
