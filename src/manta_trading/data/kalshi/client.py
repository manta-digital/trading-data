"""``KalshiClient`` — the Kalshi ``trade-api/v2`` market-data surface.

Async methods returning validated models; paged endpoints get a single-page
call plus an ``iter_*`` async generator that follows ``cursor`` until it is
absent or empty. Filter parameters mirror the documented query parameters
(design 261, Discovery Findings) verbatim — the ``*Query`` TypedDicts below
are the documented names, typed, nothing more. The request core (rate
limiting, retry, error taxonomy) lives in ``transport.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Self, TypedDict, Unpack, cast

import httpx

from manta_trading.config import Settings
from manta_trading.data.kalshi.auth import KalshiCredentials, load_credentials
from manta_trading.data.kalshi.constants import (
    CURSOR_FIELD,
    EVENT_PATH,
    EVENTS_PATH,
    HISTORICAL_CUTOFF_PATH,
    KALSHI_BASE_URL,
    KALSHI_MAX_RETRIES,
    MARKET_CANDLESTICKS_PATH,
    MARKET_PATH,
    MARKETS_PATH,
    SERIES_LIST_PATH,
    SERIES_PATH,
    TRADES_PATH,
    CandlePeriod,
    EventStatusFilter,
    MarketStatusFilter,
)
from manta_trading.data.kalshi.models import (
    Candlestick,
    CandlesticksResponse,
    Event,
    EventResponse,
    EventsPage,
    HistoricalCutoff,
    Market,
    MarketResponse,
    MarketsPage,
    Series,
    SeriesListResponse,
    SeriesResponse,
    Trade,
    TradesPage,
)
from manta_trading.data.kalshi.transport import KalshiTransport, ParamValue
from manta_trading.providers.types import RateLimit
from manta_trading.util.ratelimiter import RateLimiter


class SeriesQuery(TypedDict, total=False):
    """``GET /series`` filters (no pagination)."""

    category: str | None
    tags: str | None
    min_updated_ts: int | None
    include_product_metadata: bool | None
    include_volume: bool | None


class EventsQuery(TypedDict, total=False):
    """``GET /events`` filters; ``limit`` 1–200."""

    status: EventStatusFilter | None
    series_ticker: str | None
    tickers: str | None
    min_close_ts: int | None
    min_updated_ts: int | None
    with_nested_markets: bool | None
    limit: int | None


class MarketsQuery(TypedDict, total=False):
    """``GET /markets`` filters; ``limit`` 0–1000.

    Per Discovery Findings ``min_updated_ts`` is incompatible with every
    other filter except ``mve_filter="exclude"``; the client passes such
    combinations through and the API rejects them.
    """

    status: MarketStatusFilter | None
    event_ticker: str | None
    series_ticker: str | None
    tickers: str | None
    min_created_ts: int | None
    max_created_ts: int | None
    min_close_ts: int | None
    max_close_ts: int | None
    min_settled_ts: int | None
    max_settled_ts: int | None
    min_updated_ts: int | None
    mve_filter: str | None
    limit: int | None


class TradesQuery(TypedDict, total=False):
    """``GET /markets/trades`` filters; ``limit`` 1–1000."""

    ticker: str | None
    min_ts: int | None
    max_ts: int | None
    is_block_trade: bool | None
    limit: int | None


def _params(query: Mapping[str, object]) -> dict[str, ParamValue]:
    """A ``*Query`` TypedDict as a plain param dict.

    Every ``*Query`` value type is a ``ParamValue`` (StrEnums are ``str``);
    the cast only recovers what the TypedDict-to-Mapping widening drops.
    """
    return cast(dict[str, ParamValue], dict(query))


def _with_cursor(
    query: Mapping[str, object], cursor: str | None
) -> dict[str, ParamValue]:
    params = _params(query)
    params[CURSOR_FIELD] = cursor
    return params


class KalshiClient:
    """Async client for Kalshi market data (public or authenticated mode).

    One instance holds one rate budget shared across every surface. Close
    with :meth:`aclose`. Constructor arguments are forwarded to
    :class:`KalshiTransport`; :meth:`from_settings` selects the mode from
    the configured credential pair.
    """

    def __init__(
        self,
        *,
        base_url: str = KALSHI_BASE_URL,
        rate_limit: RateLimit | None = None,
        rate_limiter: RateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = KALSHI_MAX_RETRIES,
        credentials: KalshiCredentials | None = None,
    ) -> None:
        self._transport = KalshiTransport(
            base_url=base_url,
            rate_limit=rate_limit,
            rate_limiter=rate_limiter,
            transport=transport,
            max_retries=max_retries,
            credentials=credentials,
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, *, base_url: str = KALSHI_BASE_URL
    ) -> Self:
        """Build a client whose mode follows the configured credential pair.

        Raises ``KalshiCredentialError`` for a partial pair or unreadable
        PEM — a construction-time failure, never a runtime surprise.
        """
        return cls(
            base_url=base_url,
            credentials=load_credentials(
                settings.kalshi_api_key_id, settings.kalshi_private_key_path
            ),
        )

    @property
    def mode(self) -> str:
        """``"authenticated"`` or ``"public"``."""
        return self._transport.mode

    async def aclose(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        await self._transport.aclose()

    # ------------------------------------------------------------------
    # Series (not paginated)
    # ------------------------------------------------------------------

    async def get_series_list(self, **query: Unpack[SeriesQuery]) -> list[Series]:
        """``GET /series`` — the full list in one response (no cursor)."""
        page = await self._transport.get_model(
            SERIES_LIST_PATH, SeriesListResponse, _params(query)
        )
        return page.series

    async def get_series(self, series_ticker: str) -> Series:
        """``GET /series/{series_ticker}``."""
        resp = await self._transport.get_model(
            SERIES_PATH.format(series_ticker=series_ticker), SeriesResponse
        )
        return resp.series

    # ------------------------------------------------------------------
    # Events (cursor paginated)
    # ------------------------------------------------------------------

    async def get_events(
        self, *, cursor: str | None = None, **query: Unpack[EventsQuery]
    ) -> EventsPage:
        """``GET /events`` — one page."""
        return await self._transport.get_model(
            EVENTS_PATH, EventsPage, _with_cursor(query, cursor)
        )

    async def iter_events(self, **query: Unpack[EventsQuery]) -> AsyncIterator[Event]:
        """Follow ``cursor`` across ``GET /events`` pages."""
        cursor: str | None = None
        while True:
            page = await self.get_events(cursor=cursor, **query)
            for event in page.events:
                yield event
            if not page.cursor:
                return
            cursor = page.cursor

    async def get_event(
        self, event_ticker: str, *, with_nested_markets: bool | None = None
    ) -> Event:
        """``GET /events/{event_ticker}``.

        The wire shape is ``{"event": ..., "markets": [...]}``; a top-level
        ``markets`` list is folded onto ``Event.markets`` so callers see one
        object regardless of where the API placed it.
        """
        resp = await self._transport.get_model(
            EVENT_PATH.format(event_ticker=event_ticker),
            EventResponse,
            {"with_nested_markets": with_nested_markets},
        )
        event = resp.event
        if event.markets is None and resp.markets is not None:
            event.markets = resp.markets
        return event

    # ------------------------------------------------------------------
    # Markets (cursor paginated)
    # ------------------------------------------------------------------

    async def get_markets(
        self, *, cursor: str | None = None, **query: Unpack[MarketsQuery]
    ) -> MarketsPage:
        """``GET /markets`` — one page."""
        return await self._transport.get_model(
            MARKETS_PATH, MarketsPage, _with_cursor(query, cursor)
        )

    async def iter_markets(
        self, **query: Unpack[MarketsQuery]
    ) -> AsyncIterator[Market]:
        """Follow ``cursor`` across ``GET /markets`` pages."""
        cursor: str | None = None
        while True:
            page = await self.get_markets(cursor=cursor, **query)
            for market in page.markets:
                yield market
            if not page.cursor:
                return
            cursor = page.cursor

    async def get_market(self, ticker: str) -> Market:
        """``GET /markets/{ticker}``."""
        resp = await self._transport.get_model(
            MARKET_PATH.format(ticker=ticker), MarketResponse
        )
        return resp.market

    # ------------------------------------------------------------------
    # Candlesticks (range query, not paginated)
    # ------------------------------------------------------------------

    async def get_market_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: CandlePeriod,
        include_latest_before_start: bool = False,
    ) -> list[Candlestick]:
        """``GET /series/{series_ticker}/markets/{ticker}/candlesticks``."""
        resp = await self._transport.get_model(
            MARKET_CANDLESTICKS_PATH.format(series_ticker=series_ticker, ticker=ticker),
            CandlesticksResponse,
            {
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": int(period_interval),
                "include_latest_before_start": include_latest_before_start,
            },
        )
        return resp.candlesticks

    # ------------------------------------------------------------------
    # Trades (cursor paginated)
    # ------------------------------------------------------------------

    async def get_trades(
        self, *, cursor: str | None = None, **query: Unpack[TradesQuery]
    ) -> TradesPage:
        """``GET /markets/trades`` — one page."""
        return await self._transport.get_model(
            TRADES_PATH, TradesPage, _with_cursor(query, cursor)
        )

    async def iter_trades(self, **query: Unpack[TradesQuery]) -> AsyncIterator[Trade]:
        """Follow ``cursor`` across ``GET /markets/trades`` pages."""
        cursor: str | None = None
        while True:
            page = await self.get_trades(cursor=cursor, **query)
            for trade in page.trades:
                yield trade
            if not page.cursor:
                return
            cursor = page.cursor

    # ------------------------------------------------------------------
    # Historical tier
    # ------------------------------------------------------------------

    async def get_historical_cutoff(self) -> HistoricalCutoff:
        """``GET /historical/cutoff`` — the moving live/historical boundary.

        The remaining ``/historical/*`` fetch methods belong to slice 266.
        """
        return await self._transport.get_model(HISTORICAL_CUTOFF_PATH, HistoricalCutoff)
