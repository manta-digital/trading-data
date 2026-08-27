"""Integration tests: ``CandleRepository`` on a throwaway database (slice 264).

Uses only ``ephemeral_db`` (``MT_TIMESCALE_TEST_URL``); never the production
URL. The predicate fixture set (Task 4.4) writes real series rows — with
categories and titles — through ``write_catalog(series=...)`` so every
assertion below is by row identity, never by count: a count passes for the
wrong reason when the NULL-category market is dropped while another is
wrongly kept.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from kalshi_helpers import column, market_rows, write_catalog
from kalshi_support.samples import CANDLE_SAMPLE
from psycopg import errors

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi.candle_plan import last_complete_period, period_span
from manta_trading.data.kalshi.candle_repository import (
    CANDLE_COLUMNS,
    CandleRepository,
    StateAdvance,
)
from manta_trading.data.kalshi.candle_types import CandleRule
from manta_trading.data.kalshi.constants import (
    COLLECTED_CANDLE_PERIOD,
    MarketStatus,
    Surface,
)
from manta_trading.data.kalshi.repository import CatalogRepository

PERIOD = COLLECTED_CANDLE_PERIOD
SPAN = period_span(PERIOD)
PHASE_START = datetime(2026, 8, 27, 14, 20, 11, tzinfo=UTC)
CUTOFF = datetime(2026, 6, 25, tzinfo=UTC)
OPENED = PHASE_START - timedelta(days=2)
CLOSES = PHASE_START + timedelta(days=1)

RULE_C = CandleRule(
    traded_only=True,
    categories=frozenset(),
    excluded_categories=frozenset({"Sports", "Mentions"}),
    excluded_series_pattern=r"MENTION|SAY",
    excluded_title_pattern=r"\m(say|says|mention|mentions)\M",
)
EVERYTHING = CandleRule(False, frozenset(), frozenset(), None, None)
ONLY_SPORTS = CandleRule(False, frozenset({"Sports"}), frozenset(), None, None)
ANY_VOLUME = CandleRule(
    False,
    RULE_C.categories,
    RULE_C.excluded_categories,
    RULE_C.excluded_series_pattern,
    RULE_C.excluded_title_pattern,
)

#: The predicate fixture set: ``(market ticker, event ticker, category, title,
#: volume_24h, volume)``. The series ticker is ``f"{event}-SERIES"`` — the
#: helper's derivation — so ``KXFEDMENTION-1-SERIES`` trips the *ticker*
#: pattern while ``E-TALK-SERIES`` does not, and only its title does.
FIXTURES: list[tuple[str, str, str | None, str | None, int, int]] = [
    ("SPORTS", "E-SPORTS", "Sports", "Chiefs win?", 10, 100),
    ("MENTIONS", "E-MENTIONS", "Mentions", "Trump tariffs", 10, 100),
    ("TALK", "E-TALK", "Politics", "Will Powell say inflation?", 10, 100),
    ("FEDMENTION", "KXFEDMENTION-1", "Economics", "Fed statement", 10, 100),
    ("QUIET", "E-QUIET", "Politics", "Quiet market", 0, 0),
    ("POLITICS", "E-POLITICS", "Politics", "Senate control", 5, 50),
    ("NULLCAT", "E-NULLCAT", None, None, 3, 30),
]
ALL_TICKERS = {ticker for ticker, *_ in FIXTURES}


def _template() -> km.Market:
    return market_rows("markets_open")[0]


def fixture_markets(**overrides: Any) -> tuple[list[km.Market], list[km.Series]]:
    template = _template()
    markets = [
        template.model_copy(
            update={
                "ticker": ticker,
                "event_ticker": event,
                "status": MarketStatus.ACTIVE.value,
                "open_time": OPENED,
                "close_time": CLOSES,
                "settlement_ts": None,
                "volume_24h_fp": Decimal(volume_24h),
                "volume_fp": Decimal(volume),
                **overrides,
            }
        )
        for ticker, event, _, _, volume_24h, volume in FIXTURES
    ]
    series = [
        km.Series(ticker=f"{event}-SERIES", category=category, title=title)
        for _, event, category, title, _, _ in FIXTURES
    ]
    return markets, series


def finalized(settled: datetime, **overrides: Any) -> dict[str, Any]:
    return {
        "status": MarketStatus.FINALIZED.value,
        "close_time": settled - SPAN,
        "settlement_ts": settled,
        "volume_24h_fp": Decimal(0),
        **overrides,
    }


def candle(end: datetime, **overrides: Any) -> km.Candlestick:
    return km.Candlestick.model_validate(
        {**CANDLE_SAMPLE, "end_period_ts": int(end.timestamp()), **overrides}
    )


@pytest.fixture()
def repo(kalshi_conn: psycopg.AsyncConnection[Any]) -> CandleRepository:
    return CandleRepository(kalshi_conn, RULE_C)


def with_rule(kalshi_conn: psycopg.AsyncConnection[Any], rule: CandleRule):
    return CandleRepository(kalshi_conn, rule)


async def live_tickers(
    kalshi_conn: psycopg.AsyncConnection[Any], rule: CandleRule
) -> set[str]:
    rows = await with_rule(kalshi_conn, rule).pending_live(PERIOD, PHASE_START)
    return {r.ticker for r in rows}


class TestPredicateLive:
    """Criterion 2 under the recent-trade form, by row identity."""

    async def test_default_rule_keeps_politics_and_the_null_series(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        assert await live_tickers(kalshi_conn, RULE_C) == {"POLITICS", "NULLCAT"}

    async def test_allow_list_does_not_match_null(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        assert await live_tickers(kalshi_conn, ONLY_SPORTS) == {"SPORTS"}

    async def test_traded_only_false_admits_the_never_traded(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        assert await live_tickers(kalshi_conn, ANY_VOLUME) == {
            "POLITICS",
            "NULLCAT",
            "QUIET",
        }

    async def test_every_setting_empty_returns_all(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        assert await live_tickers(kalshi_conn, EVERYTHING) == ALL_TICKERS

    async def test_rows_carry_window_inputs(
        self, kalshi_repo: CatalogRepository, repo: CandleRepository
    ):
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        rows = {r.ticker: r for r in await repo.pending_live(PERIOD, PHASE_START)}
        assert rows["POLITICS"].open_time == OPENED
        assert rows["POLITICS"].close_time == CLOSES
        assert rows["POLITICS"].watermark_ts is None

    async def test_invalid_regex_is_the_databases_own_error(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        """A configuration bug must be loud: PostgreSQL's ``invalid regular
        expression`` propagates (SQLSTATE 2201B, a ``DataError``) — nothing
        swallows it."""
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        broken = CandleRule(False, frozenset(), frozenset(), "(", None)
        with pytest.raises(errors.InvalidRegularExpression):
            await with_rule(kalshi_conn, broken).pending_live(PERIOD, PHASE_START)


class TestPredicateEver:
    """The same set as finalized rows under the ever-traded form."""

    async def test_backlog_under_default_rule(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        markets, series = fixture_markets(**finalized(CUTOFF + timedelta(days=1)))
        await write_catalog(kalshi_repo, markets, series)
        rows = await with_rule(kalshi_conn, RULE_C).pending_backlog(
            PERIOD, PHASE_START, CUTOFF, limit=100
        )
        assert {r.ticker for r in rows} == {"POLITICS", "NULLCAT"}
        # Finalized rows are never in the live set.
        assert await live_tickers(kalshi_conn, EVERYTHING) == set()

    async def test_every_setting_empty_returns_all(
        self, kalshi_repo: CatalogRepository, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        markets, series = fixture_markets(**finalized(CUTOFF + timedelta(days=1)))
        await write_catalog(kalshi_repo, markets, series)
        rows = await with_rule(kalshi_conn, EVERYTHING).pending_backlog(
            PERIOD, PHASE_START, CUTOFF, limit=100
        )
        assert {r.ticker for r in rows} == ALL_TICKERS

    async def test_finalized_before_cutoff_is_never_backlog(
        self, kalshi_repo: CatalogRepository, repo: CandleRepository
    ):
        """Criterion 9: behind the cutoff → not requested, but counted."""
        markets, series = fixture_markets(**finalized(CUTOFF - timedelta(days=1)))
        await write_catalog(kalshi_repo, markets, series)
        assert await repo.pending_backlog(PERIOD, PHASE_START, CUTOFF, 100) == []
        assert await repo.count_behind_cutoff(PERIOD, CUTOFF) == 2
        assert await repo.count_backlog_remaining(PERIOD, CUTOFF) == 0


class TestPendingSets:
    async def test_finishing_is_finalized_with_state_short_of_close(
        self, kalshi_repo: CatalogRepository, repo: CandleRepository
    ):
        settled = PHASE_START - timedelta(hours=2)
        markets, series = fixture_markets(**finalized(settled))
        await write_catalog(kalshi_repo, markets, series)
        close_end = settled  # close_time + period
        short = StateAdvance("POLITICS", close_end - timedelta(hours=1), OPENED)
        done = StateAdvance("NULLCAT", close_end, OPENED)
        async with repo.transaction():
            await repo.advance_state(PERIOD, [short, done])
        finishing = await repo.pending_finishing(PERIOD, PHASE_START)
        assert [r.ticker for r in finishing] == ["POLITICS"]
        assert finishing[0].watermark_ts == short.watermark_ts
        # With a state row they are no longer backlog.
        backlog = await repo.pending_backlog(PERIOD, PHASE_START, CUTOFF, 100)
        assert backlog == []

    async def test_close_time_moved_later_becomes_pending_again(
        self, kalshi_repo: CatalogRepository, repo: CandleRepository
    ):
        closed = PHASE_START - timedelta(hours=3)
        markets, series = fixture_markets(close_time=closed)
        await write_catalog(kalshi_repo, markets, series)
        async with repo.transaction():
            await repo.advance_state(
                PERIOD, [StateAdvance("POLITICS", closed + SPAN, OPENED)]
            )
        assert "POLITICS" not in {
            r.ticker for r in await repo.pending_live(PERIOD, PHASE_START)
        }
        moved = next(m for m in markets if m.ticker == "POLITICS").model_copy(
            update={"close_time": closed + timedelta(hours=1)}
        )
        async with kalshi_repo.transaction():
            await kalshi_repo.upsert_markets([moved])
        assert "POLITICS" in {
            r.ticker for r in await repo.pending_live(PERIOD, PHASE_START)
        }

    async def test_live_market_at_the_last_complete_period_is_not_pending(
        self, kalshi_repo: CatalogRepository, repo: CandleRepository
    ):
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        mark = last_complete_period(PHASE_START, PERIOD)
        async with repo.transaction():
            await repo.advance_state(PERIOD, [StateAdvance("POLITICS", mark, OPENED)])
        assert {r.ticker for r in await repo.pending_live(PERIOD, PHASE_START)} == {
            "NULLCAT"
        }

    async def test_backlog_cap_and_the_falling_remainder(
        self, kalshi_repo: CatalogRepository, repo: CandleRepository
    ):
        """Criterion 8: ``pending_backlog`` returns at most the cap while
        ``count_backlog_remaining`` reports the full remainder — and it falls
        once a batch gains state rows, oldest settlement first."""
        template = _template()
        settled = [CUTOFF + timedelta(days=i + 1) for i in range(5)]
        markets = [
            template.model_copy(
                update={
                    "ticker": f"B{i}",
                    "event_ticker": "E-B",
                    "open_time": OPENED,
                    **finalized(ts, volume_fp=Decimal(10)),
                }
            )
            for i, ts in enumerate(settled)
        ]
        series = [km.Series(ticker="E-B-SERIES", category="Politics", title="b")]
        await write_catalog(kalshi_repo, markets, series)
        first = await repo.pending_backlog(PERIOD, PHASE_START, CUTOFF, limit=2)
        assert [r.ticker for r in first] == ["B0", "B1"]
        assert await repo.count_backlog_remaining(PERIOD, CUTOFF) == 5
        async with repo.transaction():
            await repo.advance_state(
                PERIOD,
                [StateAdvance(r.ticker, r.close_time + SPAN, OPENED) for r in first],
            )
        assert await repo.count_backlog_remaining(PERIOD, CUTOFF) == 3
        second = await repo.pending_backlog(PERIOD, PHASE_START, CUTOFF, limit=2)
        assert [r.ticker for r in second] == ["B2", "B3"]


class TestWrites:
    async def test_candle_columns_parity(
        self, kalshi_conn: psycopg.AsyncConnection[Any]
    ):
        """Every mapped column exists and every non-key column is mapped — so
        a column added without a mapping (or vice versa) fails here."""
        columns = set(
            await column(
                kalshi_conn,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'kalshi' AND table_name = 'candlesticks'",
            )
        )
        mapped = {name for name, _ in CANDLE_COLUMNS}
        assert mapped == columns - {"market_ticker", "period", "end_period_ts"}

    async def test_conflict_ignore_and_flattening(
        self,
        kalshi_repo: CatalogRepository,
        repo: CandleRepository,
        kalshi_conn: psycopg.AsyncConnection[Any],
    ):
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        end = datetime(2026, 8, 27, 14, tzinfo=UTC)
        batch = [
            ("POLITICS", candle(end)),
            ("POLITICS", candle(end + SPAN, price={})),
            ("NULLCAT", candle(end)),
        ]
        async with repo.transaction():
            assert await repo.insert_candles(PERIOD, batch) == 3
        async with repo.transaction():
            assert await repo.insert_candles(PERIOD, batch) == 0
        assert await column(
            kalshi_conn, "SELECT count(*) FROM kalshi.candlesticks"
        ) == [3]
        rows = await column(
            kalshi_conn,
            "SELECT yes_bid_open_dollars FROM kalshi.candlesticks "
            "WHERE market_ticker = 'POLITICS' ORDER BY end_period_ts",
        )
        assert rows == [Decimal("0.1000"), Decimal("0.1000")]
        # A ``price: {}`` candle stores NULL price columns and its volume.
        quote_only = await column(
            kalshi_conn,
            "SELECT price_previous_dollars FROM kalshi.candlesticks "
            "WHERE market_ticker = 'POLITICS' AND end_period_ts = %s",
            end + SPAN,
        )
        assert quote_only == [None]
        assert await repo.insert_candles(PERIOD, []) == 0

    async def test_advance_state_sets_coverage_once(
        self,
        kalshi_repo: CatalogRepository,
        repo: CandleRepository,
        kalshi_conn: psycopg.AsyncConnection[Any],
    ):
        """Criterion 6: ``coverage_from_ts`` is set on first write and never
        moved by a later write with a different start."""
        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        first = StateAdvance("POLITICS", PHASE_START - timedelta(hours=1), OPENED)
        async with repo.transaction():
            await repo.advance_state(PERIOD, [first])
        later = StateAdvance("POLITICS", PHASE_START, OPENED + timedelta(hours=5))
        async with repo.transaction():
            await repo.advance_state(PERIOD, [later])
        state = "FROM kalshi.market_candle_state WHERE market_ticker = 'POLITICS'"
        assert await column(kalshi_conn, f"SELECT watermark_ts {state}") == [
            later.watermark_ts
        ]
        assert await column(kalshi_conn, f"SELECT coverage_from_ts {state}") == [
            first.coverage_from_ts
        ]
        assert await column(kalshi_conn, f"SELECT period {state}") == [int(PERIOD)]
        await repo.advance_state(PERIOD, [])

    async def test_set_sync_state(
        self, repo: CandleRepository, kalshi_repo: CatalogRepository
    ):
        async with repo.transaction():
            await repo.set_sync_state(PHASE_START, CUTOFF)
        state = await kalshi_repo.get_sync_state(Surface.CANDLESTICKS)
        assert state is not None
        assert state.last_full_sync_at == PHASE_START
        assert state.watermark_ts == CUTOFF
        assert state.cursor is None


class TestCompression:
    """Criterion 13 (Decision 4): the policy compresses an old chunk and the
    rows read back identical. The job is resolved by hypertable name and
    ``proc_name`` at use time — job ids regenerate whenever a policy is
    recreated, so none is ever recorded."""

    async def test_policy_compresses_an_old_chunk_and_rows_survive(
        self,
        kalshi_db: str,
        kalshi_repo: CatalogRepository,
        repo: CandleRepository,
        kalshi_conn: psycopg.AsyncConnection[Any],
    ):
        from manta_trading.data.kalshi.constants import KALSHI_CANDLE_COMPRESS_AFTER

        markets, series = fixture_markets()
        await write_catalog(kalshi_repo, markets, series)
        # Old enough that the whole 7-day chunk ends before the horizon.
        old = datetime.now(UTC).replace(second=0, microsecond=0) - (
            KALSHI_CANDLE_COMPRESS_AFTER + timedelta(days=16)
        )
        rows = [("POLITICS", candle(old + SPAN * i)) for i in range(10)]
        rows.append(("POLITICS", candle(old + SPAN * 10, price={})))
        async with repo.transaction():
            assert await repo.insert_candles(PERIOD, rows) == len(rows)
        query = (
            "SELECT end_period_ts, yes_bid_open_dollars, price_close_dollars, "
            "volume_fp FROM kalshi.candlesticks WHERE market_ticker = 'POLITICS' "
            "ORDER BY end_period_ts"
        )
        with psycopg.connect(kalshi_db, autocommit=True) as conn:
            before = conn.execute(query).fetchall()
            job = conn.execute(
                "SELECT job_id FROM timescaledb_information.jobs "
                "WHERE hypertable_schema = 'kalshi' "
                "AND hypertable_name = 'candlesticks' "
                "AND proc_name = 'policy_compression'"
            ).fetchall()
            assert len(job) == 1
            conn.execute("CALL run_job(%s)", (job[0][0],))
            stats = conn.execute(
                "SELECT chunk_name, compression_status "
                "FROM chunk_compression_stats('kalshi.candlesticks')"
            ).fetchall()
            after = conn.execute(query).fetchall()
            scheduled = conn.execute(
                "SELECT scheduled FROM timescaledb_information.jobs WHERE job_id = %s",
                (job[0][0],),
            ).fetchone()
        assert len(before) == len(rows)
        assert [status for _, status in stats] == ["Compressed"]
        assert after == before
        # The policy is left enabled (266 pauses it for a backfill, never here).
        assert scheduled == (True,)
