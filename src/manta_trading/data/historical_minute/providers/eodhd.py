"""EODHD minute data provider.

Implements ``IMinuteDataProvider`` against EODHD's
``/api/intraday/{TICKER}?interval=1m`` endpoint.

EODHD-specific quirks
---------------------
- **Timestamps are UTC-native.** Each bar carries a Unix-epoch ``timestamp``
  field (UTC) plus a redundant ``datetime`` field also formatted as UTC. The
  converter takes the unix int directly via
  ``pd.to_datetime(..., unit="s", utc=True)`` — no timezone localisation, no
  DST gymnastics.
- **Single-request cap is 120 calendar days.** Verified by probe
  ``scripts/probe_eodhd_chunk_size.py``. 121+ days returns HTTP 422 with
  ``{"errors": {"to": ["Max period length is 120 days"]}}``. Server-enforced;
  there is no silent truncation.
- **Five API calls per ``/intraday`` request.** This is EODHD's documented
  per-request cost, not per-symbol — relevant when computing daily quota
  utilisation against the paid plan's 100K-call/day limit.
- **Ticker format is ``SYMBOL.EXCHANGE``.** US equities omit the suffix in
  most upstream code; ``_build_url`` appends ``.US`` if no exchange is
  present. Non-US tickers (e.g. ``BMW.XETRA``) pass through unchanged.
- **Intraday is unadjusted only.** Unlike AlphaVantage's
  ``TIME_SERIES_INTRADAY`` which silently returns adjusted bars when
  ``adjusted=true`` (the default), EODHD's ``/intraday`` endpoint has no
  ``adjusted`` parameter. Adjusted columns are computed downstream by the
  adjustment layer (slice 127, ``manta_trading.data.adjustment``).

MCP-info-only rule
------------------
This provider speaks REST directly to ``https://eodhd.com/api/...``. It does
**not** import any MCP client. The ``eodhd-api`` skill is a developer-tooling
documentation source; the runtime path is REST. Do not wire MCP into runtime
"to share a code path" — that re-introduces a hosted dependency where none
is needed.

Per-month vs 120-day decision
-----------------------------
The orchestrator's ``_compute_chunk_ranges`` is provider-window-driven.
This provider declares ``max_days_per_request = 120`` because the
``/intraday`` endpoint accepts that span in a single request. AlphaVantage's
provider declares 30 (legacy month-aligned behaviour); the orchestrator
chunks accordingly without either provider needing to know about the other.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlencode

import httpx
import pandas as pd

from manta_trading.data.historical_minute.provider import (
    RateLimitInfo,
    RawDataResponse,
    ValidationResult,
)
from manta_trading.util.ratelimiter import RateLimiter

_logger = logging.getLogger(__name__)

_BASE_URL = "https://eodhd.com/api"
_INTRADAY_PATH = "intraday"
_INTERVAL = "1m"
_DEFAULT_REQUESTS_PER_MINUTE = 30
# Daily request budget: paid plan allows 100K API calls / day, and each
# /intraday request is documented as 5 API calls → ~20K /intraday
# requests/day. Reported via get_rate_limits for downstream quota tracking.
_REQUESTS_PER_DAY = 20_000
_REQUEST_TIMEOUT_S = 60.0
_DEFAULT_US_SUFFIX = "US"


class EODHDMinuteProvider:
    """EODHD implementation of ``IMinuteDataProvider``.

    Returns raw (as-traded) UTC bars. The minute writer pairs each insert
    with an adjusted-column update via the ``data.adjustment`` package.
    """

    # EODHD /intraday hard cap, server-enforced. See module docstring.
    max_days_per_request: int = 120

    def __init__(
        self,
        api_key: str,
        *,
        requests_per_minute: int = _DEFAULT_REQUESTS_PER_MINUTE,
    ) -> None:
        if not api_key:
            raise ValueError("EODHD api_key is required")
        self._api_key = api_key
        self._requests_per_minute = requests_per_minute
        self._current_usage = 0
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter(
            max_calls=requests_per_minute, period=60.0
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise_symbol(self, symbol: str) -> str:
        """Append the default ``.US`` exchange suffix when no suffix is
        present. Non-US tickers (e.g. ``BMW.XETRA``) pass through.
        """
        if "." in symbol:
            return symbol
        return f"{symbol}.{_DEFAULT_US_SUFFIX}"

    def _build_url(
        self, symbol: str, start: datetime, end: datetime
    ) -> str:
        """Construct the full ``/intraday`` URL for one chunk request.

        The API key is included in the query string (EODHD's documented
        auth scheme). Callers that log this URL must redact the key first;
        ``_log_safe_url`` returns a sanitised form for that purpose.
        """
        ticker = self._normalise_symbol(symbol)
        params = {
            "interval": _INTERVAL,
            "from": int(start.timestamp()),
            "to": int(end.timestamp()),
            "fmt": "json",
            "api_token": self._api_key,
        }
        return f"{_BASE_URL}/{_INTRADAY_PATH}/{ticker}?{urlencode(params)}"

    def _log_safe_url(self, url: str) -> str:
        """Return ``url`` with the API key redacted for safe logging."""
        return url.replace(self._api_key, "***")

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily construct the shared ``httpx.AsyncClient``."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S)
        return self._client

    # ------------------------------------------------------------------
    # IMinuteDataProvider implementation
    # ------------------------------------------------------------------

    async def fetch_minute_data(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> RawDataResponse:
        """Fetch a single chunk of 1-minute bars.

        The orchestrator is responsible for splitting longer ranges into
        chunks of at most ``max_days_per_request`` calendar days; this
        method validates that contract defensively.

        Returns a ``RawDataResponse`` whose ``raw_data`` is the **list of
        bar dicts** EODHD returns. (Not nested under a key — EODHD's
        success shape is a top-level JSON array.)
        """
        if not symbol:
            raise ValueError("symbol cannot be empty")
        span_days = (end_date - start_date).total_seconds() / 86400.0
        if span_days > self.max_days_per_request:
            raise ValueError(
                f"requested span {span_days:.2f}d exceeds "
                f"max_days_per_request={self.max_days_per_request}; "
                "orchestrator should chunk before calling fetch_minute_data"
            )

        url = self._build_url(symbol, start_date, end_date)
        async with self._rate_limiter:
            client = await self._get_client()
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                _logger.exception(
                    "HTTP error fetching %s: %s",
                    symbol,
                    self._log_safe_url(url),
                )
                raise RuntimeError(
                    f"EODHD request failed for {symbol}: {exc}"
                ) from exc

        if response.status_code != 200:
            body_preview = response.text[:300]
            if response.status_code == 422:
                # 422 indicates the orchestrator over-chunked or sent an
                # otherwise malformed range — programmer error, not a
                # transient API issue.
                _logger.error(
                    "EODHD 422 (programmer error: range exceeds 120d cap) "
                    "for %s url=%s body=%s",
                    symbol,
                    self._log_safe_url(url),
                    body_preview,
                )
            raise RuntimeError(
                f"EODHD HTTP {response.status_code} for {symbol}: "
                f"{body_preview}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"EODHD returned non-JSON for {symbol}: {response.text[:200]}"
            ) from exc

        self._current_usage += 1
        bar_count = len(body) if isinstance(body, list) else 0
        return RawDataResponse(
            symbol=symbol,
            provider="eodhd",
            start_date=start_date,
            end_date=end_date,
            raw_data=body,
            metadata={
                "fetch_time": datetime.now().isoformat(),
                "bar_count": bar_count,
                "symbol": symbol,
            },
        )

    def validate_response(self, raw_data: object) -> ValidationResult:
        """Validate a raw EODHD response.

        Success shape: ``list[dict]`` of bar records. Empty list is a
        warning (the symbol may not have traded that day) but not an error.
        Error shapes: ``{"error": "...", "code": NNN}`` or
        ``{"errors": {"to": [...], "from": [...]}}`` (the 422 envelope).
        """
        errors: list[str] = []
        warnings: list[str] = []

        if isinstance(raw_data, dict):
            if "errors" in raw_data:
                errors.append(
                    f"EODHD validation envelope: {raw_data['errors']!r}"
                )
            elif "error" in raw_data:
                code = raw_data.get("code")
                errors.append(
                    f"EODHD error (code={code}): {raw_data['error']!r}"
                )
            else:
                errors.append(
                    f"EODHD returned dict instead of list: keys="
                    f"{sorted(raw_data.keys())!r}"
                )
        elif isinstance(raw_data, list):
            if not raw_data:
                warnings.append(
                    "Empty bar list — symbol may not have traded in range"
                )
            else:
                first = raw_data[0]
                if not isinstance(first, dict):
                    errors.append(
                        f"Expected list of dicts, got list of "
                        f"{type(first).__name__}"
                    )
                else:
                    required = {"timestamp", "open", "high", "low", "close",
                                "volume"}
                    missing = required - set(first.keys())
                    if missing:
                        errors.append(
                            f"First bar missing required fields: "
                            f"{sorted(missing)!r}"
                        )
        else:
            errors.append(
                f"Unexpected EODHD response type: {type(raw_data).__name__}"
            )

        return ValidationResult(
            is_valid=not errors,
            errors=errors,
            warnings=warnings,
        )

    def convert_to_standard_format(
        self, raw_data: RawDataResponse
    ) -> pd.DataFrame:
        """Convert EODHD's bar list to the canonical OHLCV DataFrame.

        EODHD returns native UTC unix timestamps, so the conversion is a
        single ``pd.to_datetime(..., unit="s", utc=True)`` call — no
        ``tz_localize``, no ``astimezone``, no DST handling. Compare to
        the AlphaVantage converter, which had to localise from US/Eastern
        with ``ambiguous="infer"`` to guess during the fall-back DST hour.
        """
        canonical_cols = ["timestamp", "open", "high", "low", "close",
                          "volume"]
        bars = raw_data.raw_data

        if not isinstance(bars, list) or not bars:
            return pd.DataFrame(columns=canonical_cols)

        df = pd.DataFrame(bars)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df[canonical_cols]
        df = df.sort_values("timestamp").reset_index(drop=True)
        df = df.drop_duplicates(subset=["timestamp"], keep="last").reset_index(
            drop=True
        )
        for col in ("open", "high", "low", "close"):
            df[col] = df[col].astype(float)
        # EODHD emits null volume on indicative pre-market bars (no trades
        # occurred but a quote-update / imbalance produced a synthesized
        # OHLC observation). Coerce to 0 — semantically accurate and keeps
        # the int64 schema. Strategies that require executable bars should
        # filter on volume > 0.
        null_volume_count = int(df["volume"].isna().sum())
        if null_volume_count:
            _logger.info(
                "EODHD: coerced %d null-volume bar(s) to volume=0 "
                "(indicative pre-market snapshots)",
                null_volume_count,
            )
        df["volume"] = df["volume"].fillna(0).astype("int64")
        return df

    def get_rate_limits(self) -> RateLimitInfo:
        """Return current rate-limit accounting.

        ``requests_per_day`` is the derived intraday quota: paid plan
        ``dailyRateLimit`` is 100K API calls and ``/intraday`` costs 5
        calls each, giving ~20K /intraday requests/day.
        """
        return RateLimitInfo(
            requests_per_minute=self._requests_per_minute,
            requests_per_day=_REQUESTS_PER_DAY,
            current_usage=self._current_usage,
            reset_time=None,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client. Idempotent."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
