"""EODHD implementation of :class:`IDailyDataProvider`.

Slice 128 closing change: AlphaVantage was cancelled, so EODHD becomes
the daily-OHLCV source as well as the minute source. Same provider for
both granularities by default — different only when there's a strong
reason (per project guidance).

Endpoint: ``GET /eod/{ticker}?api_token=...&fmt=json[&from=YYYY-MM-DD&to=YYYY-MM-DD]``.

Symbol normalisation matches every other EODHD touch in this repo:
bare ``AAPL`` is auto-suffixed to ``AAPL.US``; an explicit suffix
(``BMW.XETRA``) passes through unchanged.

Per-row ``dividend_amount`` and ``split_coefficient`` columns are
written as ``0.0`` because EODHD's ``/eod`` payload does not carry
them per-bar — splits and dividends live in the dedicated ``splits``
and ``dividends`` tables on the daily DB, populated by
:class:`EODHDCorporateActionsProvider`. No downstream code reads the
per-row columns; slice 127's adjustment layer reads from the CA
tables. The columns remain in the schema for backward compatibility.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pandas as pd

from manta_trading.data.acquisition.daily.provider import (
    RateLimitInfo,
    ValidationResult,
)
from manta_trading.data.adjustment.providers._http import fetch_with_retry
from manta_trading.logging import get_logger
from manta_trading.providers.errors import ProviderPermanentError

_logger = get_logger(__name__)

_BASE_URL = "https://eodhd.com/api"
_DEFAULT_US_SUFFIX = "US"
_REQUEST_TIMEOUT_S = 60.0  # /eod can return many years; allow generous read

# EODHD intraday paid plan: 1000 req/min ceiling. Daily endpoints share
# the same daily quota; the per-minute limit is well above what the daily
# daemon will hit in practice.
_REQUESTS_PER_MINUTE = 1000

# "compact" / "full" map to the slice-122 IDailyDataProvider contract.
# For EODHD, "compact" → last ~100 trading days; "full" → all available.
_COMPACT_RECENT_DAYS = 200  # widen slightly to be safe past holidays


def _normalise_symbol(symbol: str) -> str:
    if "." in symbol:
        return symbol
    return f"{symbol}.{_DEFAULT_US_SUFFIX}"


class EODHDDailyProvider:
    """Concrete daily provider hitting EODHD ``/eod``.

    Stateful for one symbol fetch at a time but safe to reuse across
    many symbols in the same daemon process — the underlying
    ``httpx.AsyncClient`` is constructed lazily and reused.
    """

    def __init__(self, *, api_key: str) -> None:
        if not api_key:
            raise ValueError("EODHDDailyProvider requires an api_key")
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None
        self._current_usage = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S)
        return self._client

    def _build_url(self, symbol: str, output_size: str) -> str:
        ticker = _normalise_symbol(symbol)
        url = (
            f"{_BASE_URL}/eod/{ticker}"
            f"?api_token={self._api_key}&fmt=json"
        )
        if output_size == "compact":
            from datetime import date, timedelta
            since = (date.today() - timedelta(days=_COMPACT_RECENT_DAYS)).isoformat()
            url += f"&from={since}"
        return url

    async def fetch_daily_ohlcv(
        self,
        symbol: str,
        *,
        output_size: str = "compact",
    ) -> pd.DataFrame:
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if output_size not in ("compact", "full"):
            raise ValueError(
                f"output_size must be 'compact' or 'full', got {output_size!r}"
            )

        url = self._build_url(symbol, output_size)
        client = await self._get_client()
        raw = await fetch_with_retry(
            client=client,
            url=url,
            api_key=self._api_key,
            logger=_logger,
            timeout=_REQUEST_TIMEOUT_S,
        )

        if not isinstance(raw, list):
            raise ProviderPermanentError(
                f"unexpected /eod payload for {symbol}: "
                f"{type(raw).__name__}"
            )
        if not raw:
            # Empty list = no data available for this ticker. Surface as
            # an empty DataFrame; the orchestrator's writer will produce
            # 0 rows-written and the daemon's freshness rules handle the
            # rest. Still increment usage — the call cost was paid.
            self._current_usage += 1
            return _empty_canonical_frame()

        self._current_usage += 1
        return _to_canonical_frame(raw, symbol)

    def validate_response(self, raw_data: dict) -> ValidationResult:
        # The raw payload from EODHD's /eod is a list, not a dict. The
        # legacy IDailyDataProvider signature insists on dict; we accept
        # either and short-circuit on a list. The retry helper has
        # already classified malformed/error payloads as
        # ProviderPermanentError, so by the time data reaches the
        # downstream pipeline this method is largely a formality.
        if isinstance(raw_data, list):
            return ValidationResult(is_valid=True, errors=[], warnings=[])
        if not isinstance(raw_data, dict):
            return ValidationResult(
                is_valid=False,
                errors=[f"unexpected payload type {type(raw_data).__name__}"],
                warnings=[],
            )
        return ValidationResult(is_valid=True, errors=[], warnings=[])

    def get_rate_limits(self) -> RateLimitInfo:
        return RateLimitInfo(
            requests_per_minute=_REQUESTS_PER_MINUTE,
            requests_per_day=None,  # bounded by MT_EODHD_DAILY_LIMIT, not declared here
            current_usage=self._current_usage,
            reset_time=None,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # NB: AlphaVantageDailyProvider exposed close() (non-async). The
    # CLI shutdown path calls `await provider.close()` on the minute
    # provider but never on the daily provider; daily is closed via
    # garbage collection of its httpx client. Keeping symmetry by
    # offering aclose() too.

    async def close(self) -> None:
        await self.aclose()


_CANONICAL_COLUMNS = [
    "open", "high", "low", "close", "adjusted_close",
    "volume", "dividend_amount", "split_coefficient",
]


def _empty_canonical_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {col: pd.Series(dtype="float64") for col in _CANONICAL_COLUMNS},
        index=pd.DatetimeIndex([], name="date"),
    )


def _to_canonical_frame(rows: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
    """Convert EODHD ``/eod`` rows to the canonical DataFrame contract.

    EODHD entry shape:
        {"date": "YYYY-MM-DD", "open": ..., "high": ..., "low": ...,
         "close": ..., "adjusted_close": ..., "volume": ...}

    ``dividend_amount`` and ``split_coefficient`` are emitted as 0.0;
    the authoritative values live in the splits/dividends tables.
    """
    try:
        dates = pd.to_datetime([r["date"] for r in rows])
    except KeyError as exc:
        raise ProviderPermanentError(
            f"missing 'date' field in /eod payload for {symbol}"
        ) from exc

    def _col(key: str) -> list[float]:
        out = []
        for r in rows:
            v = r.get(key)
            out.append(0.0 if v is None else float(v))
        return out

    df = pd.DataFrame(
        {
            "open": _col("open"),
            "high": _col("high"),
            "low": _col("low"),
            "close": _col("close"),
            "adjusted_close": _col("adjusted_close"),
            "volume": _col("volume"),
            # Per-row CA columns: write 0.0; CA tables hold the truth.
            "dividend_amount": [0.0] * len(rows),
            "split_coefficient": [0.0] * len(rows),
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


__all__ = ["EODHDDailyProvider"]
