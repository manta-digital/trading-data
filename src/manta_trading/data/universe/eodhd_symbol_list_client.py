"""EODHD bulk symbol-list client for the v1 universe rebuild (slice 141).

Makes three calls per rebuild cycle:
  - /exchange-symbol-list/US          (active US equities)
  - /exchange-symbol-list/US?delisted=1 (delisted US equities)
  - /exchange-symbol-list/INDX         (indices; caller filters by Country='USA')

Pre-flight is called once before any fetch to verify plan access.
"""

from __future__ import annotations

import httpx

from manta_trading.api.http_retry import RetryPolicy, request_with_retry
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_BASE_URL = "https://eodhd.com/api"


class EodhdAccessError(Exception):
    """Raised when EODHD returns 403 Forbidden or an auth failure."""


class EodhdSchemaError(Exception):
    """Raised when EODHD returns an unexpected response shape."""


class EodhdSymbolListClient:
    """Fetches EODHD bulk symbol lists for the universe rebuild.

    Args:
        api_key: EODHD API token.
        http_policy: RetryPolicy governing timeouts and retries.
    """

    def __init__(self, api_key: str, http_policy: RetryPolicy | None = None) -> None:
        self._api_key = api_key
        self._policy = http_policy or RetryPolicy()

    async def preflight(self) -> None:
        """Verify EODHD plan access by probing the US symbol-list endpoint.

        Raises:
            EodhdAccessError: On 403 Forbidden or 401 Unauthorized.
            EodhdSchemaError: On unexpected response shape.
        """
        url = f"{_BASE_URL}/exchange-symbol-list/US"
        params = {"api_token": self._api_key, "fmt": "json"}
        async with httpx.AsyncClient() as client:
            resp = await request_with_retry(client, "GET", url, self._policy, params=params)

        if resp.status_code in (401, 403):
            raise EodhdAccessError(
                f"EODHD pre-flight failed with HTTP {resp.status_code}; "
                "check MT_EODHD_API_KEY and plan entitlements"
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise EodhdSchemaError(f"EODHD pre-flight: malformed JSON — {exc}") from exc

        if not isinstance(data, list) or not data or "Code" not in data[0]:
            raise EodhdSchemaError(
                f"EODHD pre-flight: unexpected response shape (expected list[{{Code,...}}]); "
                f"got type={type(data).__name__}"
            )

        _logger.info("EODHD pre-flight OK — %d symbols in sample", len(data))

    async def fetch_active_us(self) -> list[dict]:
        """Fetch active US equity symbols."""
        return await self._fetch("US")

    async def fetch_delisted_us(self) -> list[dict]:
        """Fetch delisted US equity symbols."""
        return await self._fetch("US", delisted=True)

    async def fetch_indx(self) -> list[dict]:
        """Fetch INDX symbols (caller filters by Country='USA')."""
        return await self._fetch("INDX")

    async def _fetch(self, exchange: str, *, delisted: bool = False) -> list[dict]:
        url = f"{_BASE_URL}/exchange-symbol-list/{exchange}"
        params: dict[str, str] = {"api_token": self._api_key, "fmt": "json"}
        if delisted:
            params["delisted"] = "1"

        async with httpx.AsyncClient() as client:
            resp = await request_with_retry(client, "GET", url, self._policy, params=params)

        if resp.status_code in (401, 403):
            raise EodhdAccessError(
                f"EODHD fetch {exchange} failed with HTTP {resp.status_code}"
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise EodhdSchemaError(
                f"EODHD fetch {exchange}: malformed JSON — {exc}"
            ) from exc

        if not isinstance(data, list):
            raise EodhdSchemaError(
                f"EODHD fetch {exchange}: expected list, got {type(data).__name__}"
            )

        _logger.info("EODHD fetch %s%s: %d rows", exchange, " (delisted)" if delisted else "", len(data))
        return data  # type: ignore[return-value]
