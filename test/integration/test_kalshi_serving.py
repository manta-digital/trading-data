"""Integration tests: the Kalshi catalog readers on a throwaway database (188).

Uses only ``kalshi_db`` (``MT_TIMESCALE_TEST_URL``); never the production URL.
Rows are seeded through ``kalshi_helpers.write_catalog`` so they are the
recorded served shapes. The readers are synchronous psycopg, so each test
seeds through the async repository and reads back over a sync connection to
the same throwaway database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from kalshi_helpers import market_rows, write_catalog

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi import serve_catalog as sc
from manta_trading.data.kalshi.constants import MarketStatus

if TYPE_CHECKING:
    from collections.abc import Iterator

    from manta_trading.data.kalshi.repository import CatalogRepository

STRIKE_EARLY = datetime(2026, 9, 1, tzinfo=UTC)
STRIKE_MID = datetime(2026, 9, 15, tzinfo=UTC)
STRIKE_LATE = datetime(2026, 9, 30, tzinfo=UTC)

#: Series carrying real categories, so ``category=`` and ``fetch_categories``
#: have something to disagree about if they are ever built differently.
_SERIES = [
    km.Series(ticker="KXFEDDECISION", category="Economics", title="Fed decision"),
    km.Series(ticker="KXCPI", category="Economics", title="CPI"),
    km.Series(ticker="KXMAYORLA", category="Politics", title="LA mayor"),
    km.Series(ticker="OTHER", category=None, title="Uncategorised"),
]

_EVENTS = [
    km.Event(
        event_ticker="KXFEDDECISION-26SEP",
        series_ticker="KXFEDDECISION",
        strike_date=STRIKE_EARLY,
    ),
    km.Event(
        event_ticker="KXFEDDECISION-26OCT",
        series_ticker="KXFEDDECISION",
        strike_date=STRIKE_MID,
    ),
    km.Event(
        event_ticker="KXFEDDECISION-26NOV",
        series_ticker="KXFEDDECISION",
        strike_date=STRIKE_LATE,
    ),
    km.Event(event_ticker="KXCPI-26SEP", series_ticker="KXCPI", strike_date=None),
]


@pytest.fixture()
def conn(kalshi_db: str) -> Iterator[psycopg.Connection]:
    """A sync connection to the throwaway database — the readers' own shape."""
    with psycopg.connect(kalshi_db) as connection:
        yield connection


@pytest.fixture()
async def seeded(
    kalshi_repo: CatalogRepository, conn: psycopg.Connection
) -> psycopg.Connection:
    """Four series, four events, and the recorded markets under one event."""
    markets = market_rows()
    async with kalshi_repo.transaction():
        await kalshi_repo.upsert_series(_SERIES)
        await kalshi_repo.upsert_events(_EVENTS)
    # The recorded markets' own event, synthesized by ``write_catalog``.
    await write_catalog(kalshi_repo, markets)
    return conn


class TestCategories:
    async def test_counts_per_distinct_category_ordered(
        self, seeded: psycopg.Connection
    ):
        rows = sc.fetch_categories(seeded)
        by_category = {row.category: row.series_count for row in rows}
        # ``write_catalog`` synthesizes one NULL-category parent series per
        # distinct event of the recorded markets, alongside the explicit
        # ``OTHER``. Derived rather than written down so a change to the
        # recorded fixture cannot silently invalidate the assertion.
        synthesized = len({m.event_ticker for m in market_rows()})
        assert by_category["Economics"] == 2
        assert by_category["Politics"] == 1
        assert by_category[None] == synthesized + 1
        assert sum(row.series_count for row in rows) == len(_SERIES) + synthesized

    async def test_ordered_by_category(self, seeded: psycopg.Connection):
        rows = sc.fetch_categories(seeded)
        named = [row.category for row in rows if row.category is not None]
        assert named == sorted(named)

    async def test_every_returned_category_is_accepted_by_the_series_filter(
        self, seeded: psycopg.Connection
    ):
        """The two must agree: the listing exists to feed the filter."""
        for row in sc.fetch_categories(seeded):
            if row.category is None:
                continue
            matched = sc.fetch_series_list(seeded, category=row.category, search=None)
            assert len(matched) == row.series_count
            assert {s.category for s in matched} == {row.category}


class TestSeeks:
    async def test_series_seek_returns_the_row(self, seeded: psycopg.Connection):
        row = sc.fetch_series(seeded, "KXFEDDECISION")
        assert row is not None
        assert row.ticker == "KXFEDDECISION"
        assert row.category == "Economics"

    async def test_event_seek_returns_the_row(self, seeded: psycopg.Connection):
        row = sc.fetch_event(seeded, "KXFEDDECISION-26SEP")
        assert row is not None
        assert row.series_ticker == "KXFEDDECISION"
        assert row.strike_date == STRIKE_EARLY

    async def test_market_seek_returns_the_row(self, seeded: psycopg.Connection):
        expected = market_rows()[0]
        row = sc.fetch_market(seeded, expected.ticker)
        assert row is not None
        assert row.ticker == expected.ticker
        assert row.event_ticker == expected.event_ticker

    @pytest.mark.parametrize("seek", [sc.fetch_series, sc.fetch_event, sc.fetch_market])
    async def test_unknown_ticker_is_none(self, seeded: psycopg.Connection, seek: Any):
        assert seek(seeded, "NO-SUCH-TICKER") is None


class TestSeriesList:
    async def test_unfiltered_lists_every_series_ordered(
        self, seeded: psycopg.Connection
    ):
        rows = sc.fetch_series_list(seeded, category=None, search=None)
        tickers = [r.ticker for r in rows]
        assert tickers == sorted(tickers)
        assert {"KXFEDDECISION", "KXCPI", "KXMAYORLA", "OTHER"} <= set(tickers)

    async def test_category_is_an_exact_match(self, seeded: psycopg.Connection):
        rows = sc.fetch_series_list(seeded, category="Economics", search=None)
        assert {r.ticker for r in rows} == {"KXFEDDECISION", "KXCPI"}

    async def test_search_is_a_ticker_prefix(self, seeded: psycopg.Connection):
        rows = sc.fetch_series_list(seeded, category=None, search="KXFED")
        assert {r.ticker for r in rows} == {"KXFEDDECISION"}

    async def test_search_is_a_prefix_not_a_substring(self, seeded: psycopg.Connection):
        assert sc.fetch_series_list(seeded, category=None, search="FEDDECISION") == []

    async def test_prefix_matching_nothing_is_an_empty_list(
        self, seeded: psycopg.Connection
    ):
        assert sc.fetch_series_list(seeded, category=None, search="ZZZZ") == []

    async def test_category_and_search_combine(self, seeded: psycopg.Connection):
        rows = sc.fetch_series_list(seeded, category="Politics", search="KXFED")
        assert rows == []


class TestEventsList:
    async def test_scoped_to_its_series_and_ordered(self, seeded: psycopg.Connection):
        rows = sc.fetch_events(
            seeded, "KXFEDDECISION", strike_from=None, strike_to=None
        )
        tickers = [r.event_ticker for r in rows]
        assert tickers == sorted(tickers)
        assert set(tickers) == {
            "KXFEDDECISION-26SEP",
            "KXFEDDECISION-26OCT",
            "KXFEDDECISION-26NOV",
        }

    async def test_strike_bounds_are_inclusive(self, seeded: psycopg.Connection):
        """A bound exactly on a row's strike date includes that row."""
        rows = sc.fetch_events(
            seeded,
            "KXFEDDECISION",
            strike_from=STRIKE_EARLY,
            strike_to=STRIKE_LATE,
        )
        assert len(rows) == 3

    async def test_strike_from_excludes_earlier(self, seeded: psycopg.Connection):
        rows = sc.fetch_events(
            seeded, "KXFEDDECISION", strike_from=STRIKE_MID, strike_to=None
        )
        assert {r.event_ticker for r in rows} == {
            "KXFEDDECISION-26OCT",
            "KXFEDDECISION-26NOV",
        }

    async def test_strike_to_excludes_later(self, seeded: psycopg.Connection):
        rows = sc.fetch_events(
            seeded, "KXFEDDECISION", strike_from=None, strike_to=STRIKE_MID
        )
        assert {r.event_ticker for r in rows} == {
            "KXFEDDECISION-26SEP",
            "KXFEDDECISION-26OCT",
        }

    async def test_null_strike_date_is_excluded_by_a_bound(
        self, seeded: psycopg.Connection
    ):
        """A NULL strike date cannot satisfy a comparison, bounded or not."""
        unbounded = sc.fetch_events(seeded, "KXCPI", strike_from=None, strike_to=None)
        bounded = sc.fetch_events(
            seeded, "KXCPI", strike_from=STRIKE_EARLY, strike_to=STRIKE_LATE
        )
        assert len(unbounded) == 1
        assert bounded == []


class TestMarketsList:
    async def test_scoped_to_its_event_and_ordered(self, seeded: psycopg.Connection):
        event_ticker = market_rows()[0].event_ticker
        rows = sc.fetch_markets(seeded, event_ticker, statuses=None)
        tickers = [r.ticker for r in rows]
        assert tickers == sorted(tickers)
        assert {r.event_ticker for r in rows} == {event_ticker}

    async def test_other_events_are_not_included(self, seeded: psycopg.Connection):
        assert sc.fetch_markets(seeded, "KXCPI-26SEP", statuses=None) == []

    async def test_multi_value_status_filter(self, seeded: psycopg.Connection):
        event_ticker = market_rows()[0].event_ticker
        every = sc.fetch_markets(seeded, event_ticker, statuses=None)
        statuses = [MarketStatus.FINALIZED.value, MarketStatus.ACTIVE.value]
        matched = sc.fetch_markets(seeded, event_ticker, statuses=statuses)
        assert matched == every

    async def test_status_filter_excludes_unmatched(self, seeded: psycopg.Connection):
        event_ticker = market_rows()[0].event_ticker
        rows = sc.fetch_markets(
            seeded, event_ticker, statuses=[MarketStatus.ACTIVE.value]
        )
        assert rows == []


class TestCountsAgreeWithFetches:
    """Every count must equal ``len()`` of its fetch over the same filter —
    the guard and the read share one predicate builder precisely so this holds.
    """

    @pytest.mark.parametrize(
        ("category", "search"),
        [
            (None, None),
            ("Economics", None),
            (None, "KX"),
            ("Economics", "KXF"),
            ("Politics", "KXFED"),
            (None, "ZZZZ"),
        ],
    )
    async def test_series(
        self, seeded: psycopg.Connection, category: str | None, search: str | None
    ):
        assert sc.count_series(seeded, category=category, search=search) == len(
            sc.fetch_series_list(seeded, category=category, search=search)
        )

    @pytest.mark.parametrize(
        ("strike_from", "strike_to"),
        [
            (None, None),
            (STRIKE_EARLY, STRIKE_LATE),
            (STRIKE_MID, None),
            (None, STRIKE_MID),
            (STRIKE_LATE, STRIKE_EARLY),
        ],
    )
    async def test_events(
        self,
        seeded: psycopg.Connection,
        strike_from: datetime | None,
        strike_to: datetime | None,
    ):
        assert sc.count_events(
            seeded, "KXFEDDECISION", strike_from=strike_from, strike_to=strike_to
        ) == len(
            sc.fetch_events(
                seeded, "KXFEDDECISION", strike_from=strike_from, strike_to=strike_to
            )
        )

    @pytest.mark.parametrize(
        "statuses",
        [
            None,
            [MarketStatus.FINALIZED.value],
            [MarketStatus.ACTIVE.value],
            [MarketStatus.FINALIZED.value, MarketStatus.ACTIVE.value],
        ],
    )
    async def test_markets(
        self, seeded: psycopg.Connection, statuses: list[str] | None
    ):
        event_ticker = market_rows()[0].event_ticker
        assert sc.count_markets(seeded, event_ticker, statuses=statuses) == len(
            sc.fetch_markets(seeded, event_ticker, statuses=statuses)
        )
