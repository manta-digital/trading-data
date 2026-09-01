"""Integration tests: ``TradeRepository`` on a throwaway database (slice 265,
Task 3.3).

Uses only ``ephemeral_db`` (``MT_TIMESCALE_TEST_URL``); never the production
URL. The predicate fixture set is ``test_kalshi_candles``' — the same seven
markets with real series rows — so the rule's row outcomes for trades are
asserted by identity against the same catalog the candle tests use.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest
from kalshi_helpers import column, write_catalog
from kalshi_support.samples import TRADE_SAMPLE
from psycopg import errors
from test_kalshi_candles import ONLY_SPORTS, RULE_C, fixture_markets

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi.constants import Surface
from manta_trading.data.kalshi.repository import CatalogRepository
from manta_trading.data.kalshi.selection import CollectionRule
from manta_trading.data.kalshi.trade_repository import (
    TRADE_COLUMNS,
    PageCounts,
    TradeRepository,
    TradeState,
)

CUTOFF = datetime(2026, 6, 25, tzinfo=UTC)
TRADED_AT = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)
WALK_START = datetime(2026, 8, 27, 15, 0, 7, tzinfo=UTC)
#: A ticker with no catalog row — the shape of the MVE tape (Decision 5).
UNKNOWN = "KXMVECROSSCATEGORY-26AUG27-X"


def trade(ticker: str, **overrides: Any) -> km.Trade:
    return km.Trade.model_validate(
        {
            **TRADE_SAMPLE,
            "ticker": ticker,
            "trade_id": str(uuid4()),
            "created_time": TRADED_AT.isoformat(),
            **overrides,
        }
    )


@pytest.fixture()
async def catalog(kalshi_repo: CatalogRepository) -> None:
    markets, series = fixture_markets()
    await write_catalog(kalshi_repo, markets, series)


@pytest.fixture()
def repo(kalshi_conn: psycopg.AsyncConnection[Any]) -> TradeRepository:
    return TradeRepository(kalshi_conn, RULE_C)


def with_rule(
    kalshi_conn: psycopg.AsyncConnection[Any], rule: CollectionRule
) -> TradeRepository:
    return TradeRepository(kalshi_conn, rule)


async def write(repo: TradeRepository, rows: list[km.Trade]) -> PageCounts:
    async with repo.transaction():
        counts = await repo.write_page(rows)
    # The full identity, for every case (Criterion 2).
    assert counts.fetched == (
        counts.written
        + counts.unknown_market
        + counts.excluded_by_rule
        + counts.duplicates
    )
    return counts


async def stored(conn: psycopg.AsyncConnection[Any]) -> list[str]:
    return await column(
        conn, "SELECT market_ticker FROM kalshi.trades ORDER BY market_ticker"
    )


@pytest.mark.usefixtures("catalog")
class TestClassification:
    """Design *Tests — Integration*, cases 1–5 and 8."""

    async def test_sports_trade_is_excluded_by_rule(
        self, repo: TradeRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        counts = await write(repo, [trade("SPORTS")])
        assert counts == PageCounts(1, 0, 1, 0, 0)
        assert await stored(kalshi_conn) == []

    async def test_unknown_market_is_counted_not_stored_not_an_error(
        self, repo: TradeRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        counts = await write(repo, [trade(UNKNOWN)])
        assert counts == PageCounts(1, 1, 0, 0, 0, unknown_tickers=(UNKNOWN,))
        assert await stored(kalshi_conn) == []

    async def test_politics_trade_is_written(
        self, repo: TradeRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        counts = await write(repo, [trade("POLITICS")])
        assert counts == PageCounts(1, 0, 0, 1, 1)
        assert await stored(kalshi_conn) == ["POLITICS"]

    async def test_second_write_of_the_same_page_is_all_duplicates(
        self, repo: TradeRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        """Criterion 3: a re-walked page writes nothing and says why."""
        page = [trade("SPORTS"), trade(UNKNOWN), trade("POLITICS"), trade("NULLCAT")]
        first = await write(repo, page)
        assert first == PageCounts(4, 1, 1, 2, 2, unknown_tickers=(UNKNOWN,))
        second = await write(repo, page)
        assert second == PageCounts(4, 1, 1, 2, 0, unknown_tickers=(UNKNOWN,))
        assert second.duplicates == 2
        assert await stored(kalshi_conn) == ["NULLCAT", "POLITICS"]

    async def test_allow_list_with_exclusions_cleared_keeps_sports(
        self, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        """Criterion 5: ``MT_KALSHI_COLLECTION_CATEGORIES=Sports`` and the
        exclusions cleared — the Sports trade is the one written."""
        counts = await write(
            with_rule(kalshi_conn, ONLY_SPORTS), [trade("SPORTS"), trade("POLITICS")]
        )
        assert counts == PageCounts(2, 0, 1, 1, 1)
        assert await stored(kalshi_conn) == ["SPORTS"]

    async def test_a_trade_is_proof_of_trading(
        self, repo: TradeRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        """The ``"any"`` form: ``QUIET`` has zero volume in the catalog and
        rule C is ``traded_only`` — its trade is stored regardless."""
        counts = await write(repo, [trade("QUIET")])
        assert counts == PageCounts(1, 0, 0, 1, 1)
        assert await stored(kalshi_conn) == ["QUIET"]

    async def test_empty_page_writes_nothing(self, repo: TradeRepository):
        assert await write(repo, []) == PageCounts(0, 0, 0, 0, 0)

    async def test_unknown_tickers_are_returned_one_per_trade(
        self, repo: TradeRepository
    ):
        """Decision 5's prefix tally counts trades, so a ticker repeats."""
        other = "KXOTHER-26AUG27-Y"
        counts = await write(repo, [trade(UNKNOWN), trade(other), trade(UNKNOWN)])
        assert counts.unknown_market == 3
        assert sorted(counts.unknown_tickers) == sorted((UNKNOWN, UNKNOWN, other))

    async def test_row_values_round_trip(
        self, repo: TradeRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        row = trade("POLITICS", taker_outcome_side=None, taker_book_side=None)
        await write(repo, [row])
        cursor = await kalshi_conn.execute(
            "SELECT trade_id::text, created_time, count_fp, yes_price_dollars, "
            "no_price_dollars, taker_outcome_side, taker_book_side, is_block_trade "
            "FROM kalshi.trades"
        )
        assert await cursor.fetchall() == [
            (
                row.trade_id,
                row.created_time,
                row.count_fp,
                row.yes_price_dollars,
                row.no_price_dollars,
                None,
                None,
                row.is_block_trade,
            )
        ]


@pytest.mark.usefixtures("catalog")
class TestLoudFailures:
    """Cases 6 and 7: bad rows fail the page — propagated, not swallowed, not
    counted, and never a storage abort."""

    async def test_non_uuid_trade_id_fails_the_page(
        self, repo: TradeRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        # Unconditional: the ``::uuid[]`` cast fails inside ``unnest``, before
        # classification — so it fires even on an unknown market's row.
        page = [trade("POLITICS"), trade(UNKNOWN, trade_id="not-a-uuid")]
        with pytest.raises(psycopg.DataError) as excinfo:
            await write(repo, page)
        assert not isinstance(excinfo.value, psycopg.OperationalError)
        assert await stored(kalshi_conn) == []

    async def test_missing_is_block_trade_on_a_selected_market_fails_the_page(
        self, repo: TradeRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        # Task 2.2: NOT NULL, never coalesced. The precondition matters —
        # only selected rows reach the INSERT, so the row must be on a market
        # the rule selects.
        with pytest.raises(errors.NotNullViolation) as excinfo:
            await write(repo, [trade("POLITICS", is_block_trade=None)])
        assert isinstance(excinfo.value, psycopg.IntegrityError)
        assert not isinstance(excinfo.value, psycopg.OperationalError)
        assert await stored(kalshi_conn) == []

    async def test_missing_is_block_trade_on_an_excluded_market_never_hits_the_column(
        self, repo: TradeRepository
    ):
        """Documents case 7's precondition: the null never touches the
        column on a row the rule drops."""
        counts = await write(repo, [trade("SPORTS", is_block_trade=None)])
        assert counts == PageCounts(1, 0, 1, 0, 0)


class TestParity:
    async def test_trade_columns_parity(
        self, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        """Every mapped column exists and every column is mapped; every
        mapped attribute is a ``Trade`` field (``taker_side`` is not one)."""
        columns = set(
            await column(
                kalshi_conn,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'kalshi' AND table_name = 'trades'",
            )
        )
        assert {name for name, _ in TRADE_COLUMNS} == columns
        assert {attribute for _, attribute in TRADE_COLUMNS} == set(
            km.Trade.model_fields
        )


class TestState:
    async def test_first_run_has_no_row(self, repo: TradeRepository):
        assert await repo.read_state() is None
        assert await repo.read_catalog_walk_start() is None

    async def test_init_state_sets_both_and_is_a_no_op_on_re_entry(
        self, repo: TradeRepository
    ):
        async with repo.transaction():
            await repo.init_state(CUTOFF, CUTOFF)
        assert await repo.read_state() == TradeState(CUTOFF, CUTOFF)
        async with repo.transaction():
            await repo.init_state(CUTOFF + timedelta(days=1), CUTOFF)
        assert await repo.read_state() == TradeState(CUTOFF, CUTOFF)

    async def test_advance_watermark_leaves_the_coverage_floor(
        self, repo: TradeRepository, kalshi_repo: CatalogRepository
    ):
        window_end = CUTOFF + timedelta(hours=1)
        async with repo.transaction():
            await repo.init_state(CUTOFF, CUTOFF)
            await repo.advance_watermark(window_end)
            await repo.set_last_full_sync(WALK_START)
        assert await repo.read_state() == TradeState(window_end, CUTOFF)
        state = await kalshi_repo.get_sync_state(Surface.TRADES)
        assert state is not None
        assert state.last_full_sync_at == WALK_START
        assert state.cursor is None

    async def test_catalog_walk_start_is_the_catalog_row(
        self, repo: TradeRepository, kalshi_repo: CatalogRepository
    ):
        async with kalshi_repo.transaction():
            await kalshi_repo.set_last_full_sync(Surface.CATALOG, WALK_START)
        assert await repo.read_catalog_walk_start() == WALK_START


FLOOR = datetime(2026, 1, 1, tzinfo=UTC)
LIVE_FLOOR = datetime(2026, 7, 1, tzinfo=UTC)


class TestHistoricalSurfaceState:
    """Slice 267, Task 4.2: the same repository over ``surface=HISTORICAL``
    binds only its own row; the live ``trades`` row is read, never written."""

    @pytest.fixture()
    def historical(self, kalshi_conn: psycopg.AsyncConnection[Any]) -> TradeRepository:
        return TradeRepository(kalshi_conn, RULE_C, surface=Surface.HISTORICAL)

    async def test_reads_none_before_any_row(self, historical: TradeRepository):
        assert historical.surface is Surface.HISTORICAL
        assert await historical.read_state() is None
        assert await historical.read_cursor() is None
        assert await historical.read_live_coverage_from() is None

    async def test_init_state_writes_only_its_row(
        self, historical: TradeRepository, repo: TradeRepository
    ):
        async with historical.transaction():
            await historical.init_state(LIVE_FLOOR, FLOOR)
        assert await historical.read_state() == TradeState(LIVE_FLOOR, FLOOR)
        assert await repo.read_state() is None

    async def test_advance_watermark_moves_only_the_historical_row(
        self, historical: TradeRepository, repo: TradeRepository
    ):
        async with repo.transaction():
            await repo.init_state(CUTOFF, CUTOFF)
        async with historical.transaction():
            await historical.init_state(LIVE_FLOOR, FLOOR)
            await historical.advance_watermark(LIVE_FLOOR - timedelta(hours=1))
            await historical.set_last_full_sync(WALK_START)
        assert await historical.read_state() == TradeState(
            LIVE_FLOOR - timedelta(hours=1), FLOOR
        )
        assert await repo.read_state() == TradeState(CUTOFF, CUTOFF)

    async def test_live_coverage_from_is_the_trades_row(
        self, historical: TradeRepository, repo: TradeRepository
    ):
        assert await historical.read_live_coverage_from() is None
        async with repo.transaction():
            await repo.init_state(CUTOFF, LIVE_FLOOR)
        assert await historical.read_live_coverage_from() == LIVE_FLOOR
        # And it is the live row, not this surface's own floor.
        async with historical.transaction():
            await historical.init_state(LIVE_FLOOR, FLOOR)
        assert await historical.read_live_coverage_from() == LIVE_FLOOR

    async def test_cursor_round_trips_and_none_clears_it(
        self, historical: TradeRepository
    ):
        async with historical.transaction():
            await historical.init_state(LIVE_FLOOR, FLOOR)
            await historical.set_cursor("page-2")
        assert await historical.read_cursor() == "page-2"
        assert await historical.read_state() == TradeState(LIVE_FLOOR, FLOOR)
        async with historical.transaction():
            await historical.set_cursor(None)
        assert await historical.read_cursor() is None
        assert await historical.read_state() == TradeState(LIVE_FLOOR, FLOOR)

    async def test_set_cursor_before_init_creates_the_row(
        self, historical: TradeRepository
    ):
        """``ON CONFLICT`` like the other state statements: a cursor saved on
        a fresh surface creates its row with no watermark."""
        async with historical.transaction():
            await historical.set_cursor("page-1")
        assert await historical.read_cursor() == "page-1"
        assert await historical.read_state() == TradeState(None, None)
        # Set once (slice 267, Decision 9): the walk's row has NULL instants
        # until the tape is seeded; init_state fills them and never
        # overwrites a set one afterwards.
        async with historical.transaction():
            await historical.init_state(LIVE_FLOOR, FLOOR)
            await historical.init_state(LIVE_FLOOR + timedelta(days=9), FLOOR)
        assert await historical.read_state() == TradeState(LIVE_FLOOR, FLOOR)
        assert await historical.read_cursor() == "page-1"


class _CountingConnection:
    """Forwards everything to the real connection; counts ``execute`` calls."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn
        self.statements = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.statements += 1
        return await self._conn.execute(*args, **kwargs)


@pytest.mark.usefixtures("catalog")
class TestOneStatementPerPage:
    async def test_a_full_page_is_one_statement(
        self, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        counting = _CountingConnection(kalshi_conn)
        repo = TradeRepository(cast("psycopg.AsyncConnection[Any]", counting), RULE_C)
        page = [
            trade(
                "POLITICS", created_time=(TRADED_AT + timedelta(seconds=i)).isoformat()
            )
            for i in range(1_000)
        ]
        counts = await write(repo, page)
        assert counts == PageCounts(1_000, 0, 0, 1_000, 1_000)
        assert counting.statements == 1
        assert await column(kalshi_conn, "SELECT count(*) FROM kalshi.trades") == [
            1_000
        ]
