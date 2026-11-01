"""Integration tests: ranked Kalshi title search on a throwaway database (#63).

Seeds a small catalog through the async repository and reads it back with the
synchronous reader, as ``test_kalshi_serving.py`` does. Uses only ``kalshi_db``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import psycopg
import pytest
from kalshi_helpers import market_rows

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi import serve_search as ss
from manta_trading.data.kalshi.constants import MarketStatus

if TYPE_CHECKING:
    from collections.abc import Iterator

    from manta_trading.data.kalshi.repository import CatalogRepository

NOW = datetime.now(UTC)

_SERIES = [
    km.Series(ticker="KXFED", title="Federal funds rate", category="Economics"),
    km.Series(ticker="KXBTC", title="Bitcoin price", category="Crypto"),
    km.Series(ticker="KXCPI", title="Consumer price index", category="Economics"),
]

_EVENTS = [
    km.Event(
        event_ticker="KXFED-27JAN",
        series_ticker="KXFED",
        title="Fed funds rate after Jan 2027 meeting?",
        strike_date=NOW + timedelta(days=60),
    ),
    km.Event(
        event_ticker="KXBTC-OLD",
        series_ticker="KXBTC",
        title="Bitcoin price on Jan 1?",
        strike_date=NOW - timedelta(days=300),
    ),
    km.Event(
        event_ticker="KXBTC-SOON",
        series_ticker="KXBTC",
        title="Bitcoin price on Dec 1?",
        strike_date=NOW + timedelta(days=2),
    ),
    km.Event(
        event_ticker="KXCPI-27JAN",
        series_ticker="KXCPI",
        title="CPI for January 2027",
        strike_date=NOW + timedelta(days=70),
    ),
]


def _market(ticker: str, event: str, title: str, status: MarketStatus):
    base = market_rows()[0]
    return base.model_copy(
        update={
            "ticker": ticker,
            "event_ticker": event,
            "title": title,
            "yes_sub_title": title,
            "no_sub_title": title,
            "subtitle": None,
            "status": status,
        }
    )


_MARKETS = [
    _market(
        "KXFED-27JAN-T6",
        "KXFED-27JAN",
        "Will the federal funds rate be above 6.00% following the Jan meeting?",
        MarketStatus.ACTIVE,
    ),
    _market(
        "KXBTC-SOON-T100",
        "KXBTC-SOON",
        "Will Bitcoin be above $100,000 on Dec 1?",
        MarketStatus.ACTIVE,
    ),
    _market(
        "KXBTC-OLD-T100",
        "KXBTC-OLD",
        "Will Bitcoin be above $100,000 on Jan 1?",
        MarketStatus.FINALIZED,
    ),
]


@pytest.fixture()
def conn(kalshi_db: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(kalshi_db) as connection:
        yield connection


@pytest.fixture()
async def seeded(
    kalshi_repo: CatalogRepository, conn: psycopg.Connection
) -> psycopg.Connection:
    async with kalshi_repo.transaction():
        await kalshi_repo.upsert_series(_SERIES)
        await kalshi_repo.upsert_events(_EVENTS)
        await kalshi_repo.upsert_markets(_MARKETS)
    return conn


class TestSearch:
    async def test_series_event_and_market_hits_are_grouped(
        self, seeded: psycopg.Connection
    ):
        hits = ss.search(seeded, "bitcoin", limit=10)
        assert [s.ticker for s in hits.series] == ["KXBTC"]
        assert {e.event_ticker for e in hits.events} == {"KXBTC-OLD", "KXBTC-SOON"}
        assert {m.ticker for m in hits.markets} == {"KXBTC-SOON-T100"}

    async def test_hits_carry_their_place_in_the_hierarchy(
        self, seeded: psycopg.Connection
    ):
        hits = ss.search(seeded, "bitcoin", limit=10)
        assert hits.events[0].series_ticker == "KXBTC"
        assert hits.markets[0].event_ticker == "KXBTC-SOON"
        assert hits.markets[0].series_ticker == "KXBTC"

    async def test_only_open_markets_are_searched(self, seeded: psycopg.Connection):
        hits = ss.search(seeded, "bitcoin january", limit=10)
        assert "KXBTC-OLD-T100" not in {m.ticker for m in hits.markets}

    async def test_stemming_matches_word_forms(self, seeded: psycopg.Connection):
        hits = ss.search(seeded, "rates", limit=10)
        assert "KXFED" in {s.ticker for s in hits.series}

    async def test_typo_still_finds_the_title(self, seeded: psycopg.Connection):
        hits = ss.search(seeded, "bitcon", limit=10)
        assert "KXBTC" in {s.ticker for s in hits.series}

    async def test_series_ticker_prefix_matches(self, seeded: psycopg.Connection):
        hits = ss.search(seeded, "kxcp", limit=10)
        assert [s.ticker for s in hits.series] == ["KXCPI"]

    async def test_ties_rank_the_nearest_upcoming_event_first(
        self, seeded: psycopg.Connection
    ):
        hits = ss.search(seeded, "bitcoin price", limit=10)
        assert [e.event_ticker for e in hits.events] == ["KXBTC-SOON", "KXBTC-OLD"]

    async def test_limit_applies_per_group(self, seeded: psycopg.Connection):
        hits = ss.search(seeded, "bitcoin", limit=1)
        assert len(hits.events) == 1

    async def test_no_match_is_three_empty_groups(self, seeded: psycopg.Connection):
        hits = ss.search(seeded, "zzzzqqqq", limit=10)
        assert (hits.series, hits.events, hits.markets) == ([], [], [])

    async def test_hostile_text_is_inert(self, seeded: psycopg.Connection):
        hits = ss.search(seeded, "'; DROP TABLE kalshi.series; -- & | !", limit=10)
        assert hits.series == []
        assert ss.search(seeded, "bitcoin", limit=10).series


class TestIndexesAreUsable:
    """The reader's SQL must be one the migration's indexes can serve.

    Events only: on a handful of rows the planner will not choose the open-
    market partial index over the status index, so a test cannot prove it.
    Both spell their ``status`` predicate from ``OPEN_MARKET_PREDICATE``; the
    partial index was seen serving the query on a 1.5M-row table (#63).
    """

    @pytest.mark.parametrize(
        ("sql", "index"),
        [
            (ss._EVENTS.exact_sql, "events_search_idx"),
        ],
    )
    async def test_planner_picks_the_text_index(
        self, seeded: psycopg.Connection, sql: str, index: str
    ):
        with seeded.transaction():
            # Plain index scans off too: on a handful of rows the status index
            # beats any text index on cost; only a bitmap scan proves usability.
            seeded.execute("SET LOCAL enable_seqscan = off")
            seeded.execute("SET LOCAL enable_indexscan = off")
            plan = "\n".join(
                row[0]
                for row in seeded.execute(
                    "EXPLAIN " + sql, {"q": "bitcoin", "limit": 10}
                )
            )
        assert index in plan
