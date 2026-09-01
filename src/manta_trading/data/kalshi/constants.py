"""Kalshi domain constants — the single source for every comparison value.

Every endpoint path, rate budget, timeout, env-var name, and lifecycle value
for the Kalshi domain is defined here and only here (CLAUDE.md: changing a
value must require editing exactly one place). Provenance for each value is
the slice 261 design's Discovery Findings (verified against docs.kalshi.com
on 2026-08-24).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

#: Client mode names, reported in logs and by ``KalshiClient.mode``.
KALSHI_MODE_PUBLIC = "public"
KALSHI_MODE_AUTHENTICATED = "authenticated"

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
    """Market lifecycle status as *served* in market objects.

    Discovery (live survey 2026-08-24, recorded in the 261 design): the
    documented filter vocabulary (``unopened``/``open``/``paused``/``closed``/
    ``settled``) is **not** what the ``status`` field carries. Served values
    map from the filters as unopened→``initialized``, open→``active``,
    paused→``inactive``, closed→``closed``/``determined`` (determined = result
    known, settlement pending), settled→``finalized``.

    Values are stored as TEXT in ``kalshi.markets.status``; the CHECK
    constraint in the kalshi migration track is derived from this enum (the
    ``acquisition_state`` precedent in ``data/acquisition/state.py``). An
    undocumented new status fails the upsert loudly by design.
    """

    INITIALIZED = "initialized"
    ACTIVE = "active"
    INACTIVE = "inactive"
    CLOSED = "closed"
    DETERMINED = "determined"
    FINALIZED = "finalized"


class MarketStatusFilter(StrEnum):
    """``status`` query-parameter values accepted by ``GET /markets``.

    Distinct from :class:`MarketStatus` (the served vocabulary) — see its
    docstring for the mapping between the two.
    """

    UNOPENED = "unopened"
    OPEN = "open"
    PAUSED = "paused"
    CLOSED = "closed"
    SETTLED = "settled"


class EventStatusFilter(StrEnum):
    """``status`` query-parameter values accepted by ``GET /events``."""

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
    #: Slice 267: the historical backfill phase's own row (Decision 7).
    HISTORICAL = "historical"


# ---------------------------------------------------------------------------
# Catalog sync (slice 262 — each value cites the design decision it serves)
# ---------------------------------------------------------------------------

#: Decision 1: the full walk covers every *live* status; ``settled`` is never
#: walked — settled markets arrive through the windowed settled stream.
CATALOG_WALK_FILTERS: tuple[MarketStatusFilter, ...] = (
    MarketStatusFilter.UNOPENED,
    MarketStatusFilter.OPEN,
    MarketStatusFilter.PAUSED,
    MarketStatusFilter.CLOSED,
)

#: Decision 2: multi-leg parlay (MVE) markets are excluded on *every* markets
#: request. The value is the documented ``mve_filter`` parameter value.
KALSHI_MVE_FILTER = "exclude"

#: ``GET /markets`` page size (documented maximum).
MARKETS_PAGE_LIMIT = 1000
#: ``GET /events`` page size (documented maximum).
EVENTS_PAGE_LIMIT = 200
#: Decision 9: ``tickers`` batch size for parent resolution and vanished-market
#: lookups. Verified live to 117 (events) / 300 (markets); one conservative
#: constant for both.
TICKERS_BATCH_SIZE = 100

#: Decision 4: the settled stream is drained in windows of this length, oldest
#: first; the watermark advances once per fully walked window.
SETTLED_WINDOW = timedelta(hours=6)
#: Decision 4: ``min_settled_ts`` / ``min_updated_ts`` are strict "after" at
#: second granularity, so each window (and the events refresh floor) starts
#: one second before its boundary. The upsert makes the overlap free.
WINDOW_OVERLAP = timedelta(seconds=1)

#: Decision 10: an awaiting market older than this (``now - close_time``) is
#: reported as past the stuck threshold. Reporting only — never retirement.
KALSHI_SETTLEMENT_STUCK_AFTER = timedelta(days=7)
#: Decision 10: age-histogram edges for ``mt data kalshi status``. The middle
#: edge *is* the stuck threshold — referenced, not repeated.
AWAITING_AGE_BUCKETS: tuple[timedelta, ...] = (
    timedelta(days=1),
    KALSHI_SETTLEMENT_STUCK_AFTER,
    timedelta(days=30),
)

#: Decision 11: preflight fails (exit 1) when the database does not answer
#: within this many seconds.
DB_CONNECT_TIMEOUT_SECONDS = 10
#: Decision 11: session-level advisory lock taken by every sync run so two
#: syncs never write concurrently. A fixed bigint; the namespace is this key
#: alone (``pg_try_advisory_lock(SYNC_ADVISORY_LOCK_KEY)``).
SYNC_ADVISORY_LOCK_KEY = 262_000_001

# ---------------------------------------------------------------------------
# Candlestick collection (slice 264 — each value cites its decision or the
# design's Discovery Findings, measured live 2026-08-26)
# ---------------------------------------------------------------------------

#: Batch candlestick endpoint (Discovery Findings): one request serves one
#: window for up to ``CANDLE_BATCH_MAX_TICKERS`` markets.
MARKETS_CANDLESTICKS_PATH = "/markets/candlesticks"

#: Decision 1: one period is collected; coarser bars are derived locally.
COLLECTED_CANDLE_PERIOD = CandlePeriod.MINUTE

#: Documented ``market_tickers`` ceiling on the batch endpoint, verified live.
CANDLE_BATCH_MAX_TICKERS = 100
#: Verified by provoking it: the batch endpoint answers HTTP 400 when
#: ``len(tickers) × periods_in_window`` of the **request** exceeds this. The
#: cap is on what is asked for, not on how many candles come back (candles
#: are sparse; a request under the cap may serve a handful) — this is the
#: fact the planner is built around (Decision 7).
CANDLE_BATCH_MAX_CANDLES = 10_000
#: Verified cap on the single-market endpoint (periods in the requested
#: range). Recorded for completeness only: the phase uses the batch path
#: exclusively, so nothing under ``data/kalshi`` reads this constant.
CANDLE_SINGLE_MAX_CANDLES = 5_000

#: Decision 5: a market with no state row is fetched from at most this far
#: before the phase start (or from its open, whichever is later).
CANDLE_FIRST_SIGHT_LOOKBACK = timedelta(hours=24)
#: Decision 6: the finalized backlog is capped at this many requests per
#: pass; the live and finishing sets are never capped.
CANDLE_BACKLOG_REQUESTS_PER_PASS = 1_000
#: One INFO progress line per this many batch requests.
CANDLE_PROGRESS_EVERY_REQUESTS = 100
#: ``status``: a tracked, still-selected open market whose watermark is older
#: than ``now - this`` is reported as lagging (two hourly firings behind).
CANDLE_LAG_STALE_AFTER = timedelta(hours=2)

#: Decision 4: ``kalshi.candlesticks`` chunk interval (journal 20260719 rule).
KALSHI_CANDLE_CHUNK_INTERVAL = timedelta(days=7)
#: Decision 4: compression policy horizon. The policy stays on while the
#: historical phase writes old candles (267 Decision 4); the manual pause
#: lever is runbook 100's.
KALSHI_CANDLE_COMPRESS_AFTER = timedelta(days=14)

# ---------------------------------------------------------------------------
# Public trades collection (slice 265 — each value cites its decision or the
# design's Discovery Findings, measured live 2026-08-27/28)
# ---------------------------------------------------------------------------

#: ``GET /markets/trades`` page size: the verified ceiling — 1,001 answers
#: HTTP 400 (Discovery Findings).
TRADE_PAGE_LIMIT = 1_000
#: Decision 1: the exchange-wide tape is walked oldest-first in windows of
#: this length; one window is ~300–550 pages at the measured volume
#: (300–550 k trades/hour) and is the unit a phase abort loses — the
#: watermark advances only after a window is fully walked. The lower bound
#: of each window steps back by 262's ``WINDOW_OVERLAP`` (``min_ts`` is a
#: strict "after"); the upsert makes the overlap free.
TRADE_WINDOW = timedelta(hours=1)
#: Decision 5: the pass's upper bound trails the catalog phase's walk start
#: by this much, so every trade classified has had its market walked into
#: the catalog first.
TRADE_LATE_ARRIVAL_GUARD = timedelta(minutes=1)
#: Decision 8: at most this many page requests per pass, checked before each
#: window — the drain paces itself under the hourly timer.
TRADE_REQUESTS_PER_PASS = 3_000
#: ``status``: a tape watermark older than ``now - this`` (two hourly
#: firings behind) is reported as behind.
TRADE_LAG_STALE_AFTER = timedelta(hours=2)
#: Decision 4: ``kalshi.trades`` chunk interval (journal 20260719 rule).
KALSHI_TRADE_CHUNK_INTERVAL = timedelta(days=7)
#: Decision 4: compression policy horizon. The policy stays on while the
#: historical phase writes old trades (267 Decision 4 — 265's rehearsal
#: measured no penalty); the manual pause lever is runbook 100's.
KALSHI_TRADE_COMPRESS_AFTER = timedelta(days=14)

# ---------------------------------------------------------------------------
# Historical backfill (slice 267 — each value cites its decision)
# ---------------------------------------------------------------------------

#: Decision 9: the settled-market archive, paged newest-first in the same
#: ``MarketsPage`` shape as ``/markets``; it takes no settlement window.
HISTORICAL_MARKETS_PATH = "/historical/markets"
#: Decision 5: the archived tape, with the same query parameters as
#: ``/markets/trades``.
HISTORICAL_TRADES_PATH = "/historical/trades"
#: Candles for one market behind the cutoff. No series segment — 261's
#: Discovery verified the path without one, unlike the live endpoint.
HISTORICAL_MARKET_CANDLESTICKS_PATH = "/historical/markets/{ticker}/candlesticks"
#: Decision 2 (PM-ratified 20260831): the phase's request cap is the
#: client's budget over this many minutes — ``rate_limit.requests_per_minute
#: × HISTORICAL_PHASE_MINUTES`` (30,000 authenticated, 9,000 public),
#: computed once at construction, never written as a literal.
HISTORICAL_PHASE_MINUTES = 30
#: Decision 9 (Architecture step 1): at most this many behind-cutoff markets
#: get candles per firing while the trades drain is still descending; once
#: the floor is reached the sub-drain is bounded by the request cap alone.
HISTORICAL_CANDLE_MARKETS_PER_PASS = 1_000
#: Decision 3 (PM-ratified 20260831): the backward trades drain stops at this
#: instant. Extending the range later is an edit to this one value.
HISTORICAL_TRADES_FLOOR = datetime(2026, 1, 1, tzinfo=UTC)
#: Decision 9: the archive walk is done once every market on a page settled
#: before ``HISTORICAL_TRADES_FLOOR - this`` — margin for the archive's
#: coarse, minute-level-overlapping order.
HISTORICAL_ARCHIVE_STOP_MARGIN = timedelta(days=1)
#: Decision 4: one market's candle fetch-and-write taking longer than this is
#: logged as slow — the signal to reach for runbook 100's manual lever.
HISTORICAL_SLOW_MARKET_SECONDS = 30
