"""``FakeCatalogSource`` — an in-memory ``CatalogSource`` for sync-core tests.

Serves the recorded 261 fixtures by default (``series_list``, the
``markets_page*`` finalized markets as the settled stream, ``markets_open``
as the ``open`` walk, ``events_page*``, ``historical_cutoff``) plus whatever
a test adds. Every received query is recorded so tests can assert
``mve_filter``, ``status``, ``min_settled_ts``/``max_settled_ts``, and
``tickers`` batches. Paging follows the real cursor contract (a ``cursor``
string, absent on the last page); the page size is the query ``limit``
unless ``page_size`` forces smaller pages.

Windowed settled responses mirror the survey: strict ``after``/``before``
at second granularity, newest first. ``tickers`` lookups silently omit
unknown tickers, as the live API does.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Unpack

from manta_trading.data.kalshi.client import EventsQuery, MarketsQuery
from manta_trading.data.kalshi.constants import MarketStatusFilter
from manta_trading.data.kalshi.models import (
    Event,
    EventsPage,
    HistoricalCutoff,
    Market,
    MarketsPage,
    Series,
)
from manta_trading.providers.errors import ProviderPermanentError

from ._samples import EVENT_SAMPLE, MARKET_SAMPLE, SERIES_SAMPLE

FIXTURE_DIR = Path(__file__).resolve().parents[4] / "test" / "fixtures" / "kalshi"

#: Page size when a query carries no ``limit`` (the API default is 100).
_DEFAULT_PAGE = 100


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Row builders (live samples with overrides)
# ---------------------------------------------------------------------------


def make_series(ticker: str, **overrides: object) -> Series:
    return Series.model_validate({**SERIES_SAMPLE, "ticker": ticker, **overrides})


def make_event(event_ticker: str, series_ticker: str, **overrides: object) -> Event:
    return Event.model_validate(
        {
            **EVENT_SAMPLE,
            "event_ticker": event_ticker,
            "series_ticker": series_ticker,
            **overrides,
        }
    )


def make_market(ticker: str, event_ticker: str, **overrides: object) -> Market:
    return Market.model_validate(
        {**MARKET_SAMPLE, "ticker": ticker, "event_ticker": event_ticker, **overrides}
    )


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _page(
    items: list[Any], cursor: str | None, limit: int | None
) -> tuple[list[Any], str | None]:
    start = int(cursor) if cursor else 0
    size = limit or _DEFAULT_PAGE
    chunk = items[start : start + size]
    end = start + size
    return chunk, (str(end) if end < len(items) else None)


class FakeCatalogSource:
    """See the module docstring."""

    def __init__(
        self, *, page_size: int | None = None, load_fixtures: bool = True
    ) -> None:
        self.page_size = page_size
        self.series: dict[str, Series] = {}
        self.events: dict[str, Event] = {}
        self.live: dict[MarketStatusFilter, list[Market]] = {
            f: [] for f in MarketStatusFilter
        }
        self.settled: list[Market] = []
        self.lookup: dict[str, Market] = {}
        self.cutoff = HistoricalCutoff.model_validate(load_fixture("historical_cutoff"))
        # Recorded traffic.
        self.calls: list[str] = []
        self.markets_queries: list[dict[str, object]] = []
        self.events_queries: list[dict[str, object]] = []
        self.series_requests: list[str] = []
        self._failures: list[
            tuple[
                str,
                BaseException,
                int | None,
                Callable[[dict[str, object]], bool] | None,
            ]
        ] = []
        self._counts: dict[str, int] = {}
        if load_fixtures:
            self._load_fixtures()

    def _load_fixtures(self) -> None:
        for row in load_fixture("series_list")["series"]:
            series = Series.model_validate(row)
            self.series[series.ticker] = series
        for page in ("events_page1", "events_page2"):
            for event in EventsPage.model_validate(load_fixture(page)).events:
                self.events[event.event_ticker] = event
        self.live[MarketStatusFilter.OPEN] = list(
            MarketsPage.model_validate(load_fixture("markets_open")).markets
        )
        for page in ("markets_page1", "markets_page2"):
            self.settled.extend(MarketsPage.model_validate(load_fixture(page)).markets)
        self._reindex()

    # ------------------------------------------------------------------
    # Test-side setup
    # ------------------------------------------------------------------

    def add_series(self, *rows: Series) -> None:
        for row in rows:
            self.series[row.ticker] = row

    def add_events(self, *rows: Event) -> None:
        for row in rows:
            self.events[row.event_ticker] = row

    def add_live(self, status: MarketStatusFilter, *rows: Market) -> None:
        self.live[status].extend(rows)
        self._reindex()

    def add_settled(self, *rows: Market) -> None:
        self.settled.extend(rows)
        self._reindex()

    def add_lookup(self, *rows: Market) -> None:
        """Markets only reachable by ``tickers`` lookup (neither walked nor settled)."""
        for row in rows:
            self.lookup[row.ticker] = row

    def _reindex(self) -> None:
        for rows in self.live.values():
            for row in rows:
                self.lookup[row.ticker] = row
        for row in self.settled:
            self.lookup[row.ticker] = row

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
    # CatalogSource
    # ------------------------------------------------------------------

    async def get_series_list(self) -> list[Series]:
        self._record("get_series_list", {})
        return list(self.series.values())

    async def get_series(self, series_ticker: str) -> Series:
        self._record("get_series", {"series_ticker": series_ticker})
        self.series_requests.append(series_ticker)
        if series_ticker not in self.series:
            raise ProviderPermanentError(f"404 series {series_ticker}")
        return self.series[series_ticker]

    async def get_markets(
        self, *, cursor: str | None = None, **query: Unpack[MarketsQuery]
    ) -> MarketsPage:
        recorded: dict[str, object] = {**query, "cursor": cursor}
        self.markets_queries.append(recorded)
        self._record("get_markets", recorded)
        tickers = query.get("tickers")
        if tickers:
            wanted = tickers.split(",")
            rows = [self.lookup[t] for t in wanted if t in self.lookup]
        elif "min_settled_ts" in query or "max_settled_ts" in query:
            rows = self._settled_window(
                query.get("min_settled_ts"), query.get("max_settled_ts")
            )
        else:
            status = query.get("status")
            rows = list(self.live[status]) if status is not None else []
        chunk, next_cursor = _page(rows, cursor, self.page_size or query.get("limit"))
        return MarketsPage(markets=chunk, cursor=next_cursor)

    def _settled_window(self, min_ts: int | None, max_ts: int | None) -> list[Market]:
        """Strict ``after``/``before`` of second-granular bounds against the
        market's (microsecond) ``settlement_ts`` — a market inside the 1 s
        overlap is served by both adjacent windows, as live."""
        lower = datetime.fromtimestamp(min_ts, tz=UTC) if min_ts is not None else None
        upper = datetime.fromtimestamp(max_ts, tz=UTC) if max_ts is not None else None
        rows = [
            m
            for m in self.settled
            if m.settlement_ts is not None
            and (lower is None or m.settlement_ts > lower)
            and (upper is None or m.settlement_ts < upper)
        ]
        rows.sort(key=lambda m: m.settlement_ts or datetime.min, reverse=True)
        return rows

    async def iter_markets(
        self, **query: Unpack[MarketsQuery]
    ) -> AsyncIterator[Market]:
        cursor: str | None = None
        while True:
            page = await self.get_markets(cursor=cursor, **query)
            for market in page.markets:
                yield market
            if not page.cursor:
                return
            cursor = page.cursor

    async def get_events(
        self, *, cursor: str | None = None, **query: Unpack[EventsQuery]
    ) -> EventsPage:
        recorded: dict[str, object] = {**query, "cursor": cursor}
        self.events_queries.append(recorded)
        self._record("get_events", recorded)
        tickers = query.get("tickers")
        min_updated = query.get("min_updated_ts")
        if tickers:
            rows = [self.events[t] for t in tickers.split(",") if t in self.events]
        elif min_updated is not None:
            rows = [
                e
                for e in self.events.values()
                if e.last_updated_ts is not None
                and _epoch(e.last_updated_ts) > min_updated
            ]
        else:
            rows = list(self.events.values())
        chunk, next_cursor = _page(rows, cursor, self.page_size or query.get("limit"))
        return EventsPage(events=chunk, cursor=next_cursor)

    async def get_historical_cutoff(self) -> HistoricalCutoff:
        self._record("get_historical_cutoff", {})
        return self.cutoff
