"""Unit tests for FinnhubIpoClient."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from manta_trading.data.universe.finnhub_ipo_client import FinnhubIpoClient


def _venue_mapper(exchange: str) -> tuple[str, str]:
    """Deterministic venue mapper for tests."""
    if "nasdaq" in exchange.lower():
        return ("NASDAQ", "NASDAQ")
    return ("US", "NYSE")


class TestFinnhubIpoClientEnrich:
    @pytest.mark.asyncio
    async def test_enrich_maps_venue_when_profile_available(self):
        mock_client = AsyncMock()
        mock_client.fetch_profile.return_value = {
            "ipo": "1980-12-12",
            "exchange": "NASDAQ NMS - GLOBAL MARKET",
        }
        ipo_client = FinnhubIpoClient(mock_client, _venue_mapper)
        result = await ipo_client.enrich("AAPL")

        assert result is not None
        assert result["first_listing_date"] == date(1980, 12, 12)
        assert result["venue"] == "NASDAQ"
        assert result["trading_calendar_id"] == "NASDAQ"

    @pytest.mark.asyncio
    async def test_enrich_returns_none_when_client_returns_none(self):
        mock_client = AsyncMock()
        mock_client.fetch_profile.return_value = None
        ipo_client = FinnhubIpoClient(mock_client, _venue_mapper)
        result = await ipo_client.enrich("UNKNOWN")
        assert result is None

    @pytest.mark.asyncio
    async def test_enrich_handles_invalid_ipo_date(self):
        """Non-ISO ipo string results in first_listing_date=None (no crash)."""
        mock_client = AsyncMock()
        mock_client.fetch_profile.return_value = {
            "ipo": "not-a-date",
            "exchange": "NASDAQ NMS - GLOBAL MARKET",
        }
        ipo_client = FinnhubIpoClient(mock_client, _venue_mapper)
        result = await ipo_client.enrich("AAPL")
        assert result is not None
        assert result["first_listing_date"] is None
        assert result["venue"] == "NASDAQ"

    @pytest.mark.asyncio
    async def test_enrich_handles_empty_exchange(self):
        mock_client = AsyncMock()
        mock_client.fetch_profile.return_value = {
            "ipo": "2000-01-03",
            "exchange": "",
        }
        ipo_client = FinnhubIpoClient(mock_client, _venue_mapper)
        result = await ipo_client.enrich("XYZ")
        assert result is not None
        assert result["venue"] == "US"
        assert result["trading_calendar_id"] == "NYSE"

    @pytest.mark.asyncio
    async def test_enrich_returns_raw_exchange(self):
        """Orchestrator needs the raw Finnhub exchange string to detect non-US."""
        mock_client = AsyncMock()
        mock_client.fetch_profile.return_value = {
            "ipo": "1990-05-12",
            "exchange": "TORONTO STOCK EXCHANGE",
        }
        ipo_client = FinnhubIpoClient(mock_client, _venue_mapper)
        result = await ipo_client.enrich("RY")
        assert result is not None
        assert result["raw_exchange"] == "TORONTO STOCK EXCHANGE"

    @pytest.mark.asyncio
    async def test_enrich_skips_venue_mapper_for_non_us(self):
        """Non-US exchanges bypass the venue mapper to avoid log noise.

        Row will be DELETEd by the orchestrator anyway, so calling the
        mapper just produces a misleading warn-and-fallback log line.
        """
        mapper_calls: list[str] = []
        def tracking_mapper(exchange: str) -> tuple[str, str]:
            mapper_calls.append(exchange)
            return ("US", "NYSE")

        mock_client = AsyncMock()
        mock_client.fetch_profile.return_value = {
            "ipo": "1990-05-12",
            "exchange": "HONG KONG EXCHANGES AND CLEARING LTD",
        }
        ipo_client = FinnhubIpoClient(mock_client, tracking_mapper)
        result = await ipo_client.enrich("FOO")

        assert result is not None
        assert result["raw_exchange"] == "HONG KONG EXCHANGES AND CLEARING LTD"
        assert mapper_calls == [], (
            f"Mapper was called for a known non-US exchange (would log a "
            f"misleading warning): {mapper_calls}"
        )
