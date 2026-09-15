"""Integration tests: the Kalshi catalog readers on a throwaway database (188).

Uses only ``kalshi_db`` (``MT_TIMESCALE_TEST_URL``); never the production URL.
Rows are seeded through ``kalshi_helpers.write_catalog`` so they are the
recorded served shapes. The readers are synchronous psycopg, so each test
seeds through the async repository and reads back over a sync connection to
the same throwaway database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
from kalshi_helpers import FIXTURE_DIR, market_rows, write_catalog
from test_kalshi_candles import RULE_C, fixture_markets

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi import serve_catalog as sc
from manta_trading.data.kalshi import serve_timeseries as st
from manta_trading.data.kalshi.candle_repository import CANDLE_COLUMNS
from manta_trading.data.kalshi.constants import (
    COLLECTED_CANDLE_PERIOD,
    CandlePeriod,
    MarketStatus,
    Surface,
)
from manta_trading.data.kalshi.selection import trades_filter_sql
from manta_trading.data.kalshi.trade_repository import TRADE_COLUMNS

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


# ---------------------------------------------------------------------------
# Section 4 — time-series readers
# ---------------------------------------------------------------------------


PERIOD = int(COLLECTED_CANDLE_PERIOD)
CANDLE_TICKER = "POLITICS"


def _recorded_candles() -> list[km.Candlestick]:
    """The recorded fixture, parsed — the stored shape is the served shape."""
    payload = json.loads(
        (FIXTURE_DIR / "candlesticks.json").read_text(encoding="utf-8")
    )
    return km.CandlesticksResponse.model_validate(payload).candlesticks


@pytest.fixture()
async def timeseries(
    kalshi_repo: CatalogRepository,
    kalshi_conn: psycopg.AsyncConnection[Any],
    conn: psycopg.Connection,
) -> psycopg.Connection:
    """The candle-test catalog plus the recorded candles under one market."""
    from manta_trading.data.kalshi.candle_repository import (
        CandleRepository,
        StateAdvance,
    )

    markets, series = fixture_markets()
    await write_catalog(kalshi_repo, markets, series)

    candles = _recorded_candles()
    repo = CandleRepository(kalshi_conn, RULE_C)
    async with repo.transaction():
        await repo.insert_candles(
            COLLECTED_CANDLE_PERIOD, [(CANDLE_TICKER, c) for c in candles]
        )
        await repo.advance_state(
            COLLECTED_CANDLE_PERIOD,
            [
                StateAdvance(
                    ticker=CANDLE_TICKER,
                    watermark_ts=max(c.end_period_ts for c in candles),
                    coverage_from_ts=min(c.end_period_ts for c in candles),
                )
            ],
        )
    return conn


class TestMarketContext:
    async def test_unknown_ticker_is_none(self, timeseries: psycopg.Connection):
        assert st.market_context(timeseries, "NO-SUCH-MARKET", period=PERIOD) is None

    async def test_market_with_no_state_row_is_not_collected(
        self, timeseries: psycopg.Connection
    ):
        """A known market with no candle state is a fact, not an error (D5)."""
        context = st.market_context(timeseries, "QUIET", period=PERIOD)
        assert context is not None
        assert context.candle_collected is False
        assert context.candle_coverage_from is None
        assert context.candle_complete_through is None

    async def test_state_row_supplies_the_candle_facts(
        self, timeseries: psycopg.Connection
    ):
        candles = _recorded_candles()
        context = st.market_context(timeseries, CANDLE_TICKER, period=PERIOD)
        assert context is not None
        assert context.candle_collected is True
        assert context.candle_coverage_from == min(c.end_period_ts for c in candles)
        assert context.candle_complete_through == max(c.end_period_ts for c in candles)

    async def test_carries_the_series_category(self, timeseries: psycopg.Connection):
        context = st.market_context(timeseries, CANDLE_TICKER, period=PERIOD)
        assert context is not None
        assert context.series_category == "Politics"

    async def test_a_different_period_has_no_state_row(
        self, timeseries: psycopg.Connection
    ):
        """The state join is keyed on the period the caller asks for."""
        other = next(p for p in CandlePeriod if int(p) != PERIOD)
        context = st.market_context(timeseries, CANDLE_TICKER, period=int(other))
        assert context is not None
        assert context.candle_collected is False


class TestTapeFacts:
    async def test_no_trades_state_row_is_none(self, timeseries: psycopg.Connection):
        """A fresh install whose trades phase has never run (D10)."""
        assert st.tape_facts(timeseries) is None

    async def test_returns_the_effective_floor_and_watermark(
        self,
        kalshi_repo: CatalogRepository,
        kalshi_conn: psycopg.AsyncConnection[Any],
        conn: psycopg.Connection,
    ):
        from manta_trading.data.kalshi.trade_repository import TradeRepository

        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        now = datetime.now(UTC).replace(microsecond=0)
        live_floor = now - timedelta(days=30)
        watermark = now - timedelta(days=1)
        trades = TradeRepository(
            kalshi_conn, RULE_C, trades_excluded=frozenset(), surface=Surface.TRADES
        )
        async with trades.transaction():
            await trades.init_state(live_floor, live_floor)
            await trades.advance_watermark(watermark)

        facts = st.tape_facts(conn)
        assert facts is not None
        assert facts.coverage_from == live_floor
        assert facts.tape_complete_through == watermark


class TestTapeFiltered:
    """The helper and the SQL predicate must agree — the membership test is
    rendered once by ``trades_filter_sql`` and evaluated by both."""

    @pytest.mark.parametrize(
        ("category", "excluded", "expected"),
        [
            ("Sports", frozenset({"Sports"}), True),
            ("Politics", frozenset({"Sports"}), False),
            ("Sports", frozenset(), False),
            (None, frozenset({"Sports"}), False),
            ("Sports", frozenset({"Sports", "Crypto"}), True),
        ],
    )
    async def test_agrees_with_the_sql_predicate(
        self,
        timeseries: psycopg.Connection,
        category: str | None,
        excluded: frozenset[str],
        expected: bool,
    ):
        selection = trades_filter_sql(excluded)
        statement = psycopg.sql.SQL(
            "SELECT {test} FROM (SELECT %(category)s::text AS category) s"
        ).format(test=selection.predicate)
        row = timeseries.execute(
            statement, {**selection.params, "category": category}
        ).fetchone()
        assert row is not None
        assert row[0] == expected
        assert st.tape_filtered(category, excluded) == expected


class TestCandleWindow:
    async def test_unbounded_returns_every_stored_row(
        self, timeseries: psycopg.Connection
    ):
        rows = st.fetch_candles(
            timeseries, CANDLE_TICKER, period=PERIOD, start=None, end=None
        )
        assert len(rows) == len(_recorded_candles())

    async def test_ordered_by_period_end(self, timeseries: psycopg.Connection):
        rows = st.fetch_candles(
            timeseries, CANDLE_TICKER, period=PERIOD, start=None, end=None
        )
        stamps = [r.end_period_ts for r in rows]
        assert stamps == sorted(stamps)

    async def test_bounds_are_inclusive_at_both_edges(
        self, timeseries: psycopg.Connection
    ):
        stamps = sorted(c.end_period_ts for c in _recorded_candles())
        rows = st.fetch_candles(
            timeseries,
            CANDLE_TICKER,
            period=PERIOD,
            start=stamps[0],
            end=stamps[-1],
        )
        assert len(rows) == len(stamps)

    async def test_a_row_just_outside_each_bound_is_excluded(
        self, timeseries: psycopg.Connection
    ):
        stamps = sorted(c.end_period_ts for c in _recorded_candles())
        rows = st.fetch_candles(
            timeseries,
            CANDLE_TICKER,
            period=PERIOD,
            start=stamps[0] + timedelta(seconds=1),
            end=stamps[-1] - timedelta(seconds=1),
        )
        assert len(rows) == len(stamps) - 2
        assert rows[0].end_period_ts == stamps[1]
        assert rows[-1].end_period_ts == stamps[-2]

    async def test_values_preserve_nulls(self, timeseries: psycopg.Connection):
        """A period with no trades stores nulls for the price object (D2)."""
        rows = st.fetch_candles(
            timeseries, CANDLE_TICKER, period=PERIOD, start=None, end=None
        )
        assert any(None in row.values for row in rows)

    async def test_value_count_matches_the_repository_mapping(
        self, timeseries: psycopg.Connection
    ):
        rows = st.fetch_candles(
            timeseries, CANDLE_TICKER, period=PERIOD, start=None, end=None
        )
        assert all(len(r.values) == len(CANDLE_COLUMNS) for r in rows)

    @pytest.mark.parametrize("offset", [0, 1, -1])
    async def test_count_equals_len_fetch(
        self, timeseries: psycopg.Connection, offset: int
    ):
        stamps = sorted(c.end_period_ts for c in _recorded_candles())
        start = stamps[0] + timedelta(seconds=offset)
        end = stamps[-1] - timedelta(seconds=offset)
        assert st.count_candles(
            timeseries, CANDLE_TICKER, period=PERIOD, start=start, end=end
        ) == len(
            st.fetch_candles(
                timeseries, CANDLE_TICKER, period=PERIOD, start=start, end=end
            )
        )


class TestTradeWindow:
    """Trades are written through the real repository, so the stored shape is
    the shape the collection phase produces."""

    @pytest.fixture()
    async def traded(
        self,
        kalshi_repo: CatalogRepository,
        kalshi_conn: psycopg.AsyncConnection[Any],
        conn: psycopg.Connection,
    ) -> tuple[psycopg.Connection, list[datetime]]:
        from kalshi_support.samples import TRADE_SAMPLE

        from manta_trading.data.kalshi.trade_repository import TradeRepository

        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        base = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)
        stamps = [base + timedelta(minutes=i) for i in range(5)]
        rows = [
            km.Trade.model_validate(
                {
                    **TRADE_SAMPLE,
                    "ticker": CANDLE_TICKER,
                    "trade_id": str(uuid4()),
                    "created_time": stamp.isoformat(),
                }
            )
            for stamp in stamps
        ]
        repo = TradeRepository(
            kalshi_conn, RULE_C, trades_excluded=frozenset(), surface=Surface.TRADES
        )
        async with repo.transaction():
            await repo.write_page(rows)
        return conn, stamps

    async def test_unbounded_returns_every_stored_row(
        self, traded: tuple[psycopg.Connection, list[datetime]]
    ):
        conn, stamps = traded
        rows = st.fetch_trades(conn, CANDLE_TICKER, start=None, end=None)
        assert len(rows) == len(stamps)

    async def test_bounds_are_inclusive_at_both_edges(
        self, traded: tuple[psycopg.Connection, list[datetime]]
    ):
        conn, stamps = traded
        rows = st.fetch_trades(conn, CANDLE_TICKER, start=stamps[0], end=stamps[-1])
        assert len(rows) == len(stamps)

    async def test_a_row_just_outside_each_bound_is_excluded(
        self, traded: tuple[psycopg.Connection, list[datetime]]
    ):
        conn, stamps = traded
        rows = st.fetch_trades(
            conn,
            CANDLE_TICKER,
            start=stamps[0] + timedelta(seconds=1),
            end=stamps[-1] - timedelta(seconds=1),
        )
        assert len(rows) == len(stamps) - 2

    async def test_scoped_to_its_market(
        self, traded: tuple[psycopg.Connection, list[datetime]]
    ):
        conn, _ = traded
        assert st.fetch_trades(conn, "QUIET", start=None, end=None) == []

    async def test_value_count_matches_the_repository_mapping(
        self, traded: tuple[psycopg.Connection, list[datetime]]
    ):
        """Nine stored columns minus ``market_ticker``, carried once above."""
        conn, _ = traded
        rows = st.fetch_trades(conn, CANDLE_TICKER, start=None, end=None)
        assert all(len(r.values) == len(TRADE_COLUMNS) - 1 for r in rows)

    @pytest.mark.parametrize("offset", [0, 1, -1])
    async def test_count_equals_len_fetch(
        self, traded: tuple[psycopg.Connection, list[datetime]], offset: int
    ):
        conn, stamps = traded
        start = stamps[0] + timedelta(seconds=offset)
        end = stamps[-1] - timedelta(seconds=offset)
        assert st.count_trades(conn, CANDLE_TICKER, start=start, end=end) == len(
            st.fetch_trades(conn, CANDLE_TICKER, start=start, end=end)
        )
