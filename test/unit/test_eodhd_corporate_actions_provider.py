"""Unit tests for EODHDCorporateActionsProvider (slice 128).

Tests the I/O + parsing path in isolation by stubbing
``fetch_with_retry`` (the shared HTTP helper at
``manta_trading.data.adjustment.providers._http``). The DB persistence
layer in ``ingest.py`` is exercised separately — those helpers are pure
SQL with simple UPSERT semantics tested by the slice-127 integration
tests.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from manta_trading.data.adjustment.providers import Dividend, Split
from manta_trading.data.adjustment.providers.eodhd import (
    EODHDCorporateActionsProvider,
)
from manta_trading.providers.errors import (
    ProviderPermanentError,
    ProviderTransientError,
)


_SPLITS_FIXTURE = [
    {"date": "2020-08-31", "split": "4.000000/1.000000"},
    {"date": "2014-06-09", "split": "7.000000/1.000000"},
]

_DIVS_FIXTURE = [
    {
        "date": "2024-02-09",
        "value": "0.24000",
        "unadjustedValue": "0.24000",
        "currency": "USD",
    },
    {
        "date": "2023-11-10",
        "value": "0.24",
        "unadjustedValue": "0.24",
        "currency": "USD",
    },
]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestFetchSplitsParsing:
    def test_parses_fixture_into_split_dataclasses(self) -> None:
        provider = EODHDCorporateActionsProvider(api_key="k")
        with patch(
            "manta_trading.data.adjustment.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=_SPLITS_FIXTURE),
        ):
            splits = asyncio.run(provider.fetch_splits("AAPL"))
        assert len(splits) == 2
        assert all(isinstance(s, Split) for s in splits)
        assert splits[0].symbol == "AAPL"
        assert splits[0].ex_date == date(2020, 8, 31)
        assert splits[0].ratio_to == Decimal("4.000000")
        assert splits[0].ratio_from == Decimal("1.000000")

    def test_normalises_bare_symbol_to_us_suffix(self) -> None:
        provider = EODHDCorporateActionsProvider(api_key="k")
        captured: list[str] = []

        async def _stub(*, client, url, api_key, logger, timeout, max_retries=3):
            captured.append(url)
            return []

        with patch(
            "manta_trading.data.adjustment.providers.eodhd.fetch_with_retry",
            new=_stub,
        ):
            asyncio.run(provider.fetch_splits("AAPL"))
        assert "/splits/AAPL.US" in captured[0]

    def test_preserves_explicit_exchange_suffix(self) -> None:
        provider = EODHDCorporateActionsProvider(api_key="k")
        captured: list[str] = []

        async def _stub(*, client, url, api_key, logger, timeout, max_retries=3):
            captured.append(url)
            return []

        with patch(
            "manta_trading.data.adjustment.providers.eodhd.fetch_with_retry",
            new=_stub,
        ):
            asyncio.run(provider.fetch_splits("BMW.XETRA"))
        assert "/splits/BMW.XETRA" in captured[0]

    def test_non_list_payload_raises_permanent(self) -> None:
        provider = EODHDCorporateActionsProvider(api_key="k")
        with patch(
            "manta_trading.data.adjustment.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value={"error": "no such ticker"}),
        ):
            with pytest.raises(ProviderPermanentError, match="unexpected /splits"):
                asyncio.run(provider.fetch_splits("FAKE"))

    def test_malformed_split_entry_raises_permanent(self) -> None:
        provider = EODHDCorporateActionsProvider(api_key="k")
        with patch(
            "manta_trading.data.adjustment.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=[{"date": "2020-08-31"}]),  # no 'split'
        ):
            with pytest.raises(ProviderPermanentError, match="malformed split"):
                asyncio.run(provider.fetch_splits("AAPL"))


class TestFetchDividendsParsing:
    def test_parses_fixture(self) -> None:
        provider = EODHDCorporateActionsProvider(api_key="k")
        with patch(
            "manta_trading.data.adjustment.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=_DIVS_FIXTURE),
        ):
            divs = asyncio.run(provider.fetch_dividends("AAPL"))
        assert len(divs) == 2
        assert all(isinstance(d, Dividend) for d in divs)
        assert divs[0].symbol == "AAPL"
        assert divs[0].ex_date == date(2024, 2, 9)
        assert divs[0].amount == Decimal("0.24000")
        assert divs[0].currency == "USD"

    def test_uses_unadjusted_value_not_value(self) -> None:
        # EODHD's "value" is retroactively rebased; "unadjustedValue" is
        # the actual cash paid. The provider must read the latter.
        provider = EODHDCorporateActionsProvider(api_key="k")
        fixture = [
            {
                "date": "2020-01-01",
                "value": "0.10",  # rebased
                "unadjustedValue": "0.40",  # actual
                "currency": "USD",
            },
        ]
        with patch(
            "manta_trading.data.adjustment.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=fixture),
        ):
            divs = asyncio.run(provider.fetch_dividends("AAPL"))
        assert divs[0].amount == Decimal("0.40")

    def test_default_currency_when_missing(self) -> None:
        provider = EODHDCorporateActionsProvider(api_key="k")
        fixture = [
            {"date": "2020-01-01", "value": "0.1", "unadjustedValue": "0.1"},
        ]
        with patch(
            "manta_trading.data.adjustment.providers.eodhd.fetch_with_retry",
            new=AsyncMock(return_value=fixture),
        ):
            divs = asyncio.run(provider.fetch_dividends("AAPL"))
        assert divs[0].currency == "USD"


class TestRetryHttpHelperBehavior:
    """Verifies the shared retry helper's classification — the part the
    EODHD CA provider relies on for the slice 128 §Error handling rules.
    """

    def test_4xx_other_than_429_is_permanent(self) -> None:
        from manta_trading.data.adjustment.providers._http import (
            fetch_with_retry,
        )
        from manta_trading.logging import get_logger

        # Build a mock client returning HTTP 404
        mock_resp = httpx.Response(404, text="not found")

        class _Client:
            async def get(self, url, timeout):
                return mock_resp

        with pytest.raises(ProviderPermanentError, match="HTTP 404"):
            asyncio.run(
                fetch_with_retry(
                    client=_Client(),  # type: ignore[arg-type]
                    url="https://x/path?api_token=k",
                    api_key="k",
                    logger=get_logger("test"),
                    timeout=1.0,
                    max_retries=2,
                )
            )

    def test_429_retried_then_transient_after_exhaustion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from manta_trading.data.adjustment.providers import _http
        from manta_trading.logging import get_logger

        # Skip real sleeps
        async def _no_sleep(_):
            return None

        monkeypatch.setattr(_http, "_sleep", _no_sleep)

        attempts = {"n": 0}

        class _Client:
            async def get(self, url, timeout):
                attempts["n"] += 1
                return httpx.Response(429, text="rate limited")

        with pytest.raises(ProviderTransientError, match="HTTP 429"):
            asyncio.run(
                _http.fetch_with_retry(
                    client=_Client(),  # type: ignore[arg-type]
                    url="https://x/path?api_token=k",
                    api_key="k",
                    logger=get_logger("test"),
                    timeout=1.0,
                    max_retries=2,
                )
            )
        # 1 initial + 2 retries = 3 attempts
        assert attempts["n"] == 3

    def test_5xx_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from manta_trading.data.adjustment.providers import _http
        from manta_trading.logging import get_logger

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(_http, "_sleep", _no_sleep)

        attempts = {"n": 0}

        class _Client:
            async def get(self, url, timeout):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    return httpx.Response(503, text="temporarily down")
                return httpx.Response(200, json=[])

        result = asyncio.run(
            _http.fetch_with_retry(
                client=_Client(),  # type: ignore[arg-type]
                url="https://x/path",
                api_key="k",
                logger=get_logger("test"),
                timeout=1.0,
                max_retries=3,
            )
        )
        assert result == []
        assert attempts["n"] == 3

    def test_timeout_retried_then_transient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from manta_trading.data.adjustment.providers import _http
        from manta_trading.logging import get_logger

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(_http, "_sleep", _no_sleep)

        class _Client:
            async def get(self, url, timeout):
                raise httpx.ReadTimeout("timed out")

        with pytest.raises(ProviderTransientError, match="transport failure"):
            asyncio.run(
                _http.fetch_with_retry(
                    client=_Client(),  # type: ignore[arg-type]
                    url="https://x/path",
                    api_key="k",
                    logger=get_logger("test"),
                    timeout=1.0,
                    max_retries=2,
                )
            )

    def test_retry_after_header_honored_on_429(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from manta_trading.data.adjustment.providers import _http
        from manta_trading.logging import get_logger

        sleeps: list[float] = []

        async def _capture_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(_http, "_sleep", _capture_sleep)

        attempts = {"n": 0}

        class _Client:
            async def get(self, url, timeout):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    return httpx.Response(
                        429, text="x", headers={"Retry-After": "7"}
                    )
                return httpx.Response(200, json=[])

        asyncio.run(
            _http.fetch_with_retry(
                client=_Client(),  # type: ignore[arg-type]
                url="https://x/path",
                api_key="k",
                logger=get_logger("test"),
                timeout=1.0,
                max_retries=3,
            )
        )
        # First (and only) sleep should equal the Retry-After hint.
        assert sleeps == [7.0]
