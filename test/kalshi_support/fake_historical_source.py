"""``FakeHistoricalSource`` — an in-memory ``HistoricalSource`` (slice 267).

Composes the existing fakes rather than copying them: the archived tape is
a ``FakeTradeSource`` (its window semantics and paging), the parents
``ingest_markets`` resolves come from a ``FakeCatalogSource`` (its
event/series answers), and this class adds the two archive surfaces —
``candles_by_ticker`` served by range, and ``archive_pages`` served newest
page first behind an opaque cursor (``archive-<n>``; the last page carries
``""`` as the live API does). Every query is recorded; ``raise_on`` injects
any exception on any of the three archive methods.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Unpack

from kalshi_support.fake_source import FakeCatalogSource
from kalshi_support.fake_trade_source import FakeTradeSource
from manta_trading.data.kalshi.client import EventsQuery
from manta_trading.data.kalshi.constants import CandlePeriod
from manta_trading.data.kalshi.models import (
    Candlestick,
    Event,
    EventsPage,
    Market,
    MarketsPage,
    Series,
    Trade,
    TradesPage,
)
from manta_trading.data.kalshi.sync_types import epoch

_CURSOR_PREFIX = "archive-"


def _archive_index(cursor: str | None) -> int:
    return int(cursor.removeprefix(_CURSOR_PREFIX)) if cursor else 0


class FakeHistoricalSource:
    """See the module docstring."""

    def __init__(self, *, page_size: int | None = None) -> None:
        self.trades = FakeTradeSource(page_size=page_size)
        self.catalog = FakeCatalogSource(load_fixtures=False)
        self.candles_by_ticker: dict[str, list[Candlestick]] = {}
        self.archive_pages: list[list[Market]] = []
        #: Every archive query: ``{"cursor", "limit", "mve_filter"}``.
        self.archive_queries: list[dict[str, object]] = []
        #: Every candles query: ``{"ticker", "start_ts", "end_ts", "period"}``.
        self.candle_queries: list[dict[str, object]] = []
        self.calls: list[str] = []
        self._failures: list[
            tuple[
                str,
                BaseException,
                int | None,
                Callable[[dict[str, object]], bool] | None,
            ]
        ] = []
        self._counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Test-side setup
    # ------------------------------------------------------------------

    @property
    def trade_queries(self) -> list[dict[str, object]]:
        return self.trades.trade_queries

    def add_trades(self, *rows: Trade) -> None:
        self.trades.add_trades(*rows)

    def add_candles(self, ticker: str, *rows: Candlestick) -> None:
        self.candles_by_ticker.setdefault(ticker, []).extend(rows)

    def add_archive_page(self, *rows: Market) -> None:
        """Append one page; pages are served in the order added (newest
        first is the caller's responsibility, as it is the API's)."""
        self.archive_pages.append(list(rows))

    def raise_on(
        self,
        call: str,
        exc: BaseException,
        *,
        at: int | None = None,
        when: Callable[[dict[str, object]], bool] | None = None,
    ) -> None:
        """Raise ``exc`` on the ``at``-th invocation of ``call`` and/or when
        ``when(query)`` is true (``call`` is the method name)."""
        self._failures.append((call, exc, at, when))

    def _record(self, call: str, query: dict[str, object]) -> None:
        self.calls.append(call)
        self._counts[call] = self._counts.get(call, 0) + 1
        for name, exc, at, when in self._failures:
            if name != call:
                continue
            if at is not None and self._counts[call] != at:
                continue
            if when is not None and not when(query):
                continue
            raise exc

    # ------------------------------------------------------------------
    # HistoricalSource
    # ------------------------------------------------------------------

    async def get_historical_markets(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        mve_filter: str | None = None,
    ) -> MarketsPage:
        query: dict[str, object] = {
            "cursor": cursor,
            "limit": limit,
            "mve_filter": mve_filter,
        }
        self.archive_queries.append(query)
        self._record("get_historical_markets", query)
        index = _archive_index(cursor)
        if index >= len(self.archive_pages):
            return MarketsPage(markets=[], cursor="")
        last = index + 1 >= len(self.archive_pages)
        return MarketsPage(
            markets=list(self.archive_pages[index]),
            cursor="" if last else f"{_CURSOR_PREFIX}{index + 1}",
        )

    async def get_historical_trades(
        self, *, cursor: str | None = None, min_ts: int, max_ts: int, limit: int
    ) -> TradesPage:
        query: dict[str, object] = {
            "cursor": cursor,
            "min_ts": min_ts,
            "max_ts": max_ts,
            "limit": limit,
        }
        self._record("get_historical_trades", query)
        return await self.trades.get_trades(
            cursor=cursor, min_ts=min_ts, max_ts=max_ts, limit=limit
        )

    async def get_historical_market_candlesticks(
        self,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: CandlePeriod,
    ) -> list[Candlestick]:
        query: dict[str, object] = {
            "ticker": ticker,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period": int(period_interval),
        }
        self.candle_queries.append(query)
        self._record("get_historical_market_candlesticks", query)
        return [
            candle
            for candle in self.candles_by_ticker.get(ticker, [])
            if start_ts < epoch(candle.end_period_ts) <= end_ts
        ]

    # Parents — delegated to the catalog fake, which records them.

    async def get_events(
        self, *, cursor: str | None = None, **query: Unpack[EventsQuery]
    ) -> EventsPage:
        return await self.catalog.get_events(cursor=cursor, **query)

    async def get_event(self, event_ticker: str) -> Event:
        return await self.catalog.get_event(event_ticker)

    async def get_series(self, series_ticker: str) -> Series:
        return await self.catalog.get_series(series_ticker)
