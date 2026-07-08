"""Unit tests for EodhdSymbolListClient using respx to mock httpx."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from manta_trading.api.http_retry import RetryPolicy
from manta_trading.data.universe.eodhd_symbol_list_client import (
    EodhdAccessError,
    EodhdSchemaError,
    EodhdSymbolListClient,
)

_BASE = "https://eodhd.com/api"

_SAMPLE_ROWS = [
    {"Code": "AAPL", "Name": "Apple", "Country": "USA", "Exchange": "US", "Currency": "USD", "Type": "Common Stock"},
    {"Code": "SPY", "Name": "SPDR", "Country": "USA", "Exchange": "US", "Currency": "USD", "Type": "ETF"},
]

# Fast policy — zero backoff for tests
_FAST_POLICY = RetryPolicy(connect_timeout=1.0, read_timeout=1.0, retries=1, backoff_seconds=[0.0])


def _client() -> EodhdSymbolListClient:
    return EodhdSymbolListClient(api_key="TESTKEY", http_policy=_FAST_POLICY)


class TestPreflight:
    @pytest.mark.asyncio
    @respx.mock
    async def test_preflight_ok(self):
        respx.get(_BASE + "/exchange-symbol-list/US").mock(
            return_value=Response(200, json=_SAMPLE_ROWS)
        )
        await _client().preflight()  # Should not raise

    @pytest.mark.asyncio
    @respx.mock
    async def test_preflight_403_raises_access_error(self):
        respx.get(_BASE + "/exchange-symbol-list/US").mock(
            return_value=Response(403, text="Forbidden")
        )
        with pytest.raises(EodhdAccessError):
            await _client().preflight()

    @pytest.mark.asyncio
    @respx.mock
    async def test_preflight_malformed_json_raises_schema_error(self):
        respx.get(_BASE + "/exchange-symbol-list/US").mock(
            return_value=Response(200, content=b"NOT JSON {{{")
        )
        with pytest.raises(EodhdSchemaError):
            await _client().preflight()

    @pytest.mark.asyncio
    @respx.mock
    async def test_preflight_unexpected_shape_raises_schema_error(self):
        respx.get(_BASE + "/exchange-symbol-list/US").mock(
            return_value=Response(200, json={"error": "bad"})
        )
        with pytest.raises(EodhdSchemaError):
            await _client().preflight()


class TestFetch:
    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_active_us_returns_list(self):
        respx.get(_BASE + "/exchange-symbol-list/US").mock(
            return_value=Response(200, json=_SAMPLE_ROWS)
        )
        result = await _client().fetch_active_us()
        assert len(result) == 2
        assert result[0]["Code"] == "AAPL"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_delisted_us_includes_delisted_param(self):
        route = respx.get(_BASE + "/exchange-symbol-list/US").mock(
            return_value=Response(200, json=_SAMPLE_ROWS)
        )
        await _client().fetch_delisted_us()
        assert "delisted=1" in str(route.calls[0].request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_indx_returns_list(self):
        respx.get(_BASE + "/exchange-symbol-list/INDX").mock(
            return_value=Response(200, json=_SAMPLE_ROWS)
        )
        result = await _client().fetch_indx()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_403_raises_access_error(self):
        respx.get(_BASE + "/exchange-symbol-list/US").mock(
            return_value=Response(403, text="Forbidden")
        )
        with pytest.raises(EodhdAccessError):
            await _client().fetch_active_us()

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_malformed_json_raises_schema_error(self):
        respx.get(_BASE + "/exchange-symbol-list/US").mock(
            return_value=Response(200, content=b"<<<NOT JSON>>>")
        )
        with pytest.raises(EodhdSchemaError):
            await _client().fetch_active_us()

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_429_retried_succeeds_on_second(self):
        route = respx.get(_BASE + "/exchange-symbol-list/US").mock(
            side_effect=[
                Response(429, text="Too Many Requests"),
                Response(200, json=_SAMPLE_ROWS),
            ]
        )
        result = await _client().fetch_active_us()
        assert len(result) == 2
        assert route.call_count == 2
