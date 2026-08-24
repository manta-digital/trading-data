"""Kalshi domain constants — the single source for every comparison value.

Every endpoint path, rate budget, timeout, env-var name, and lifecycle value
for the Kalshi domain is defined here and only here (CLAUDE.md: changing a
value must require editing exactly one place). Provenance for each value is
the slice 261 design's Discovery Findings (verified against docs.kalshi.com
on 2026-08-24).
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

import httpx

from manta_trading.providers.types import RateLimit

# ---------------------------------------------------------------------------
# Base URL and endpoint paths (relative to the base URL)
# ---------------------------------------------------------------------------

#: Documented primary production base URL. The alternate host
#: (``api.elections.kalshi.com``) is a configuration change, not a code change.
KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

#: Templated paths use ``str.format`` fields named after the path parameter.
SERIES_LIST_PATH = "/series"
SERIES_PATH = "/series/{series_ticker}"
EVENTS_PATH = "/events"
EVENT_PATH = "/events/{event_ticker}"
MARKETS_PATH = "/markets"
MARKET_PATH = "/markets/{ticker}"
MARKET_CANDLESTICKS_PATH = "/series/{series_ticker}/markets/{ticker}/candlesticks"
TRADES_PATH = "/markets/trades"
HISTORICAL_CUTOFF_PATH = "/historical/cutoff"

# ---------------------------------------------------------------------------
# Rate budgets (design Technical Decision 4 — each defined exactly once)
# ---------------------------------------------------------------------------

#: Public (unauthenticated) mode. Kalshi does not document an unauthenticated
#: budget, so this is *our* conservative operating budget (5 req/s sustained),
#: not Kalshi's ceiling.
KALSHI_PUBLIC_RATE_LIMIT = RateLimit(requests_per_minute=300)

#: Authenticated mode. Kalshi's token-bucket model (2026-04-23) refills the
#: Basic tier at 200 read tokens/s at ~10 tokens per request ≈ 20 reads/s;
#: 1000/min is ≈83% of that, leaving headroom for endpoints that cost more.
KALSHI_AUTHENTICATED_RATE_LIMIT = RateLimit(requests_per_minute=1000)

#: Seconds in the rate-limit window that ``RateLimit.requests_per_minute``
#: is expressed over.
RATE_LIMIT_PERIOD_SECONDS = 60.0

# ---------------------------------------------------------------------------
# Transport policy
# ---------------------------------------------------------------------------

#: All four httpx phases set explicitly — no phase left at library default.
KALSHI_REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

#: Bounded retry on transient failures (mirrors the shape of
#: ``data/adjustment/providers/_http.fetch_with_retry``): ``MAX_RETRIES``
#: retries after the first attempt, exponential backoff capped at ``CAP``.
KALSHI_MAX_RETRIES = 3
KALSHI_BACKOFF_BASE_SECONDS = 1.0
KALSHI_BACKOFF_CAP_SECONDS = 60.0

#: HTTP statuses classified as transient (retry, then ProviderTransientError).
#: Every other non-2xx status is permanent.
KALSHI_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# ---------------------------------------------------------------------------
# Authenticated mode (design Technical Decision 4a)
# ---------------------------------------------------------------------------

#: Env var holding the Kalshi API key ID.
KALSHI_API_KEY_ID_ENV = "MT_KALSHI_API_KEY_ID"
#: Env var holding the *path* to the RSA private-key PEM file (never the key).
KALSHI_PRIVATE_KEY_PATH_ENV = "MT_KALSHI_PRIVATE_KEY_PATH"

KALSHI_ACCESS_KEY_HEADER = "KALSHI-ACCESS-KEY"
KALSHI_ACCESS_TIMESTAMP_HEADER = "KALSHI-ACCESS-TIMESTAMP"
KALSHI_ACCESS_SIGNATURE_HEADER = "KALSHI-ACCESS-SIGNATURE"

# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

#: Name of the cursor field in paged responses and of the query parameter
#: that requests the next page.
CURSOR_FIELD = "cursor"

# ---------------------------------------------------------------------------
# Domain enums
# ---------------------------------------------------------------------------


class MarketStatus(StrEnum):
    """Market lifecycle status as served by ``GET /markets``.

    Values are stored as TEXT in ``kalshi.markets.status``; the CHECK
    constraint in the kalshi migration track is derived from this enum (the
    ``acquisition_state`` precedent in ``data/acquisition/state.py``). An
    undocumented new status fails the upsert loudly by design.
    """

    UNOPENED = "unopened"
    OPEN = "open"
    PAUSED = "paused"
    CLOSED = "closed"
    SETTLED = "settled"


class EventStatus(StrEnum):
    """Event status filter accepted by ``GET /events`` (no ``paused``)."""

    UNOPENED = "unopened"
    OPEN = "open"
    CLOSED = "closed"
    SETTLED = "settled"


class CandlePeriod(IntEnum):
    """Candlestick ``period_interval`` in minutes.

    ``kalshi.market_candle_state.period`` derives its CHECK constraint from
    this enum.
    """

    MINUTE = 1
    HOUR = 60
    DAY = 1440


class Surface(StrEnum):
    """Collection surfaces tracked in ``kalshi.sync_state``.

    ``kalshi.sync_state.surface`` derives its CHECK constraint from this enum.
    """

    CATALOG = "catalog"
    CANDLESTICKS = "candlesticks"
    TRADES = "trades"
