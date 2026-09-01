"""Unit tests for ``HistoricalSync`` (slice 267, Task 6.4) and
``historical_types``.

Fake source, three fake repositories, recording sink — no network, no
database. The floor is the real constant; the live floor sits three hours
above it so a full descent is three windows.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from kalshi_support.fake_candle_repository import FakeCandleRepository, FakeMarket
from kalshi_support.fake_candle_source import make_trade_candle
from kalshi_support.fake_historical_source import FakeHistoricalSource
from kalshi_support.fake_repository import FakeCatalogRepository
from kalshi_support.fake_source import make_event, make_market, make_series
from kalshi_support.fake_trade_repository import FakeTradeRepository
from kalshi_support.fake_trade_source import make_trade
from kalshi_support.sync_harness import RecordingSink
from psycopg import errors

from manta_trading.data.kalshi import historical_candles, historical_sync
from manta_trading.data.kalshi.candle_plan import period_span
from manta_trading.data.kalshi.candle_types import CandleItemError
from manta_trading.data.kalshi.constants import (
    CANDLE_SINGLE_MAX_CANDLES,
    COLLECTED_CANDLE_PERIOD,
    HISTORICAL_ARCHIVE_STOP_MARGIN,
    HISTORICAL_CANDLE_MARKETS_PER_PASS,
    HISTORICAL_SLOW_MARKET_SECONDS,
    HISTORICAL_TRADES_FLOOR,
    KALSHI_MVE_FILTER,
    MARKETS_PAGE_LIMIT,
    WINDOW_OVERLAP,
    MarketStatus,
    Surface,
)
from manta_trading.data.kalshi.events import SyncEventType as T
from manta_trading.data.kalshi.historical_sync import HistoricalSync
from manta_trading.data.kalshi.historical_types import (
    HistoricalCatalogSource,
    HistoricalResult,
    HistoricalTradeSource,
    classify_historical,
)
from manta_trading.data.kalshi.repository import SyncState
from manta_trading.data.kalshi.selection import CollectionRule
from manta_trading.data.kalshi.sync_types import SyncOutcome, epoch
from manta_trading.data.kalshi.trade_repository import TradeState
from manta_trading.providers.errors import (
    ProviderPermanentError,
    ProviderTransientError,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
FLOOR = HISTORICAL_TRADES_FLOOR
#: Three windows above the floor: a full descent is cheap to simulate.
LIVE_FLOOR = FLOOR + 3 * HOUR
CUTOFF = datetime(2026, 6, 25, tzinfo=UTC)
PERIOD = COLLECTED_CANDLE_PERIOD
SPAN = period_span(PERIOD)
CHUNK = SPAN * CANDLE_SINGLE_MAX_CANDLES
BIG_CAP = 100_000
RULE = CollectionRule(True, frozenset(), frozenset({"Sports"}), None, None)
SERIES = "KXSER"
EVENT = "KXSER-26JUN"
STOP_BEFORE = FLOOR - HISTORICAL_ARCHIVE_STOP_MARGIN


class Harness:
    def __init__(
        self,
        *,
        cap: int = BIG_CAP,
        page_size: int | None = None,
        live_floor: datetime | None = LIVE_FLOOR,
        candles_cutoff: datetime | None = CUTOFF,
        archive_walked: bool = True,
        watermark: datetime | None = None,
    ) -> None:
        self.now = NOW
        self.cap = cap
        self.source = FakeHistoricalSource(page_size=page_size)
        self.source.catalog.add_series(make_series(SERIES))
        self.source.catalog.add_events(make_event(EVENT, SERIES))
        self.trades = FakeTradeRepository(surface=Surface.HISTORICAL)
        self.trades.unknown_tickers.add("KXMVE-1")
        self.trades.excluded_tickers.add("SPORTS")
        self.trades.live_coverage_from = live_floor
        self.candles = FakeCandleRepository()
        self.catalog = FakeCatalogRepository(now=NOW)
        if candles_cutoff is not None:
            self.catalog.sync_state[Surface.CANDLESTICKS] = SyncState(
                NOW, candles_cutoff, None
            )
        if archive_walked:
            # Done marker: cursor NULL, watermark set (design *State*).
            self.trades.state = TradeState(watermark or LIVE_FLOOR, FLOOR)
        self.run_id = uuid4()
        self.core = self.new_core()

    def new_core(self, *, cap: int | None = None) -> HistoricalSync:
        self.sink = RecordingSink()
        self.core = HistoricalSync(
            self.source,
            self.trades,
            self.candles,
            self.catalog,
            self.sink,
            rule=RULE,
            run_id=self.run_id,
            cap=self.cap if cap is None else cap,
            clock=lambda: self.now,
        )
        return self.core

    def behind_cutoff_market(
        self, ticker: str, *, chunks: int = 1, settled: datetime | None = None
    ) -> FakeMarket:
        """A finalized, selected market before the cutoff with no state row
        whose ``[open, close + period)`` spans exactly ``chunks`` requests."""
        settled = settled or CUTOFF - timedelta(days=1)
        close = settled - timedelta(minutes=5)
        open_time = close + SPAN - CHUNK * chunks
        market = FakeMarket(
            ticker,
            open_time,
            close,
            status=MarketStatus.FINALIZED.value,
            settlement_ts=settled,
        )
        self.candles.add_market(market)
        self.source.add_candles(
            ticker,
            make_trade_candle(open_time + SPAN),
            make_trade_candle(close),
            make_trade_candle(close + SPAN),
        )
        return market

    def tape(self, ticker: str, *below: timedelta) -> None:
        self.source.add_trades(
            *(make_trade(ticker, LIVE_FLOOR - offset) for offset in below)
        )

    def archive_page(self, *settled: tuple[str, datetime]) -> None:
        self.source.add_archive_page(
            *(
                make_market(
                    ticker,
                    EVENT,
                    status=MarketStatus.FINALIZED.value,
                    result="yes",
                    close_time=when - timedelta(minutes=1),
                    settlement_ts=when,
                )
                for ticker, when in settled
            )
        )

    def three_page_archive(self) -> None:
        """Pages newest first; the third settled entirely before the stop."""
        self.archive_page(("A1", CUTOFF), ("A2", CUTOFF - timedelta(days=2)))
        self.archive_page(("B1", FLOOR + 30 * HOUR), ("B2", FLOOR + 2 * HOUR))
        self.archive_page(
            ("C1", STOP_BEFORE - HOUR), ("C2", STOP_BEFORE - timedelta(days=3))
        )

    def parent_requests(self) -> int:
        return len(self.source.catalog.events_queries) + len(
            self.source.catalog.series_requests
        )


@pytest.fixture
def h() -> Harness:
    return Harness()


def state_rows(h: Harness) -> set[str]:
    return {ticker for ticker, _ in h.candles.state}


class TestStateRow:
    async def test_first_run_seeds_the_row_at_the_live_floor(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Case 1 (Criterion 2): after the (empty) archive walk the row is
        seeded at the live floor with the floor target, and both are logged."""
        h = Harness(archive_walked=False)
        h.source.add_archive_page()
        with caplog.at_level(logging.INFO, logger=historical_sync.__name__):
            result = await h.core.run()
        assert h.trades.state == TradeState(FLOOR, FLOOR)  # drained to the floor
        assert result.watermark_before == LIVE_FLOOR
        assert result.floor_reached is True
        line = next(
            m for m in (r.getMessage() for r in caplog.records) if "first run" in m
        )
        assert LIVE_FLOOR.isoformat() in line and FLOOR.isoformat() in line
        assert h.trades.cursor_log == [None]  # the walk cleared it when done

    async def test_no_live_row_skips_trades_but_drains_candles(self):
        """Case 2."""
        h = Harness(live_floor=None, archive_walked=False)
        h.source.add_archive_page()
        h.behind_cutoff_market("H1")
        result = await h.core.run()
        assert result.trades_row_missing is True
        assert h.source.trade_queries == []
        assert h.trades.state is None
        assert result.candle_markets_completed == 1
        assert result.error is None

    async def test_no_candles_row_skips_candles_but_drains_trades(self):
        """Case 3."""
        h = Harness(candles_cutoff=None)
        h.behind_cutoff_market("H1")
        result = await h.core.run()
        assert h.candles.pending_limits == []
        assert h.source.candle_queries == []
        assert result.windows_completed == 3
        assert result.floor_reached is True


class TestCandles:
    async def test_chunks_rows_and_the_stamp(self, h: Harness):
        """Case 4: three chunks, the rows land, the state row is stamped
        ``watermark = close + period, coverage_from = open``."""
        market = h.behind_cutoff_market("H1", chunks=3)
        result = await h.core.run()
        assert result.candle_requests == 3
        assert [q["ticker"] for q in h.source.candle_queries] == ["H1"] * 3
        starts = [q["start_ts"] for q in h.source.candle_queries]
        assert starts == [epoch(market.open_time + CHUNK * i) for i in range(3)]
        assert result.candles_written == 3
        row = h.candles.state[("H1", int(PERIOD))]
        assert (row.watermark_ts, row.coverage_from_ts) == (
            market.close_time + SPAN,
            market.open_time,
        )
        assert result.candle_markets_completed == 1
        assert result.candle_markets_remaining == 0

    async def test_cap_is_shared_with_the_trades_drain(self):
        """Case 5, first clause: cap = candle requests + 1 → one window."""
        h = Harness(cap=2)
        h.behind_cutoff_market("H1")
        result = await h.core.run()
        assert result.candle_requests == 1
        assert result.windows_completed == 1
        assert result.capped is True
        assert result.requests == 2
        assert h.trades.state == TradeState(LIVE_FLOOR - HOUR, FLOOR)

    async def test_cap_smaller_than_a_market_finishes_it_and_starts_no_other(self):
        """Case 5, second clause."""
        h = Harness(cap=2)
        h.behind_cutoff_market("H1", chunks=3)
        h.behind_cutoff_market("H2", chunks=3, settled=CUTOFF - timedelta(hours=1))
        result = await h.core.run()
        assert result.candle_markets_completed == 1
        assert state_rows(h) == {"H1"}
        assert len(h.source.candle_queries) == 3
        assert result.capped is True
        assert h.source.trade_queries == []
        assert result.candle_markets_remaining == 1

    async def test_slow_market_is_warned_and_counted(
        self, h: Harness, caplog: pytest.LogCaptureFixture
    ):
        """Case 9: the clock steps past the threshold during the fetch."""
        h.behind_cutoff_market("H1")
        fetch = h.source.get_historical_market_candlesticks

        async def slow(*args: Any, **kwargs: Any) -> Any:
            h.now += timedelta(seconds=HISTORICAL_SLOW_MARKET_SECONDS + 1)
            return await fetch(*args, **kwargs)

        h.source.get_historical_market_candlesticks = slow  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING, logger=historical_candles.__name__):
            result = await h.core.run()
        assert result.slow_markets == 1
        assert any("slow market H1" in r.getMessage() for r in caplog.records)

    async def test_permanent_error_is_an_item_error_transient_aborts(self):
        """Case 16 (Criterion 10)."""
        h = Harness()
        h.behind_cutoff_market("H-BAD")
        h.behind_cutoff_market("H-OK", settled=CUTOFF - timedelta(hours=1))
        h.source.raise_on(
            "get_historical_market_candlesticks",
            ProviderPermanentError("404"),
            when=lambda q: q["ticker"] == "H-BAD",
        )
        result = await h.core.run()
        assert result.item_errors == [CandleItemError("H-BAD", "404")]
        assert state_rows(h) == {"H-OK"}
        assert result.windows_completed == 3
        assert classify_historical(result, None) is SyncOutcome.PARTIAL

        again = Harness()
        again.behind_cutoff_market("H-BAD")
        again.source.raise_on(
            "get_historical_market_candlesticks", ProviderTransientError("503")
        )
        with pytest.raises(ProviderTransientError) as excinfo:
            await again.core.run()
        assert classify_historical(again.core.result, excinfo.value) is (
            SyncOutcome.PROVIDER_ABORT
        )

    async def test_per_pass_ceiling_lifts_once_the_floor_is_reached(self):
        """Case 17 (Decision 9)."""
        h = Harness()
        h.behind_cutoff_market("H1")
        await h.core.run()
        assert h.candles.pending_limits == [HISTORICAL_CANDLE_MARKETS_PER_PASS]
        at_floor = Harness(watermark=FLOOR)
        at_floor.behind_cutoff_market("H1")
        result = await at_floor.core.run()
        assert at_floor.candles.pending_limits == [None]
        assert result.floor_reached is True
        assert at_floor.source.trade_queries == []


class TestTradesDrain:
    async def test_watermark_moves_down_by_hours_and_stops_at_the_floor(self):
        """Case 6 (Criterion 3)."""
        h = Harness(watermark=FLOOR + 2 * HOUR)
        result = await h.core.run()
        assert result.windows_completed == 2
        assert result.watermark_after == FLOOR
        assert result.floor_reached is True
        assert h.trades.state == TradeState(FLOOR, FLOOR)
        assert [q["max_ts"] for q in h.source.trade_queries] == [
            epoch(FLOOR + 2 * HOUR),
            epoch(FLOOR + HOUR),
        ]
        assert h.source.trade_queries[0]["min_ts"] == epoch(
            FLOOR + HOUR - WINDOW_OVERLAP
        )
        at_floor = Harness(watermark=FLOOR)
        again = await at_floor.core.run()
        assert at_floor.source.trade_queries == []
        assert again.floor_reached is True and again.requests == 0

    async def test_counts_hold_the_identity(self):
        """Case 7 (Criterion 4)."""
        h = Harness(page_size=2)
        minute = timedelta(minutes=1)
        h.tape("POL", minute, 2 * minute, HOUR + minute)
        h.tape("SPORTS", 3 * minute)
        h.tape("KXMVE-1", 4 * minute)
        dup = make_trade("POL", LIVE_FLOOR - 5 * minute)
        h.source.add_trades(dup)
        h.trades.stored.add((dup.ticker, dup.created_time, dup.trade_id))
        result = await h.core.run()
        assert result.trades_fetched == 6
        assert result.trades_written == 3
        assert result.unknown_market == 1
        assert result.excluded_by_rule == 1
        assert result.duplicates == 1
        assert result.trades_fetched == (
            result.trades_written
            + result.unknown_market
            + result.excluded_by_rule
            + result.duplicates
        )
        assert result.unknown_prefixes == {"KXMVE": 1}

    async def test_candle_abort_precedes_trades_and_leaves_the_row(self):
        """Case 8, first clause."""
        h = Harness()
        h.behind_cutoff_market("H1")
        h.source.raise_on(
            "get_historical_market_candlesticks", ProviderTransientError("503")
        )
        with pytest.raises(ProviderTransientError):
            await h.core.run()
        assert h.source.trade_queries == []
        assert h.trades.state == TradeState(LIVE_FLOOR, FLOOR)
        assert h.core.result.error == "ProviderTransientError: 503"
        assert h.sink.types() == [T.PHASE_FINISHED]

    @pytest.mark.parametrize("failing", ["source", "repository"])
    async def test_failure_mid_window_leaves_the_previous_window_start(
        self, failing: str
    ):
        """Case 8, second clause (Decision 6): window 1 has one row; window 2
        three rows at one per page, failing on its second page."""
        h = Harness(page_size=1)
        h.tape("POL", timedelta(minutes=30))
        h.tape("POL", HOUR + timedelta(minutes=1), HOUR + timedelta(minutes=2))
        h.tape("POL", HOUR + timedelta(minutes=3))
        if failing == "source":
            h.source.raise_on(
                "get_historical_trades", ProviderTransientError("503"), at=3
            )
            expected: type[Exception] = ProviderTransientError
        else:
            h.trades.fail_on("write_page", errors.OperationalError("lost"), at=3)
            expected = errors.OperationalError
        with pytest.raises(expected):
            await h.core.run()
        assert h.trades.state == TradeState(LIVE_FLOOR - HOUR, FLOOR)
        assert len(h.trades.stored) == 2
        assert h.core.result.windows_completed == 1


class TestArchiveWalk:
    async def test_three_pages_stop_at_the_margin_and_a_second_run_skips(self):
        """Case 13 (Criterion 9)."""
        h = Harness(archive_walked=False)
        h.three_page_archive()
        result = await h.core.run()
        assert len(h.source.archive_queries) == 3
        assert h.source.archive_queries[0] == {
            "cursor": None,
            "limit": MARKETS_PAGE_LIMIT,
            "mve_filter": KALSHI_MVE_FILTER,
        }
        assert [q["cursor"] for q in h.source.archive_queries] == [
            None,
            "archive-1",
            "archive-2",
        ]
        assert result.requests == 3 + h.parent_requests() + 3  # + 3 trade windows
        assert {"A1", "A2", "B1", "B2"} <= set(h.catalog.markets)
        assert EVENT in h.catalog.events and SERIES in h.catalog.series
        assert h.trades.cursor_log == ["archive-1", "archive-2", None]
        assert result.archive_walked is True
        assert result.archive_pages == 3
        assert result.archive_markets_fetched == 6
        assert result.archive_markets_written == 6
        again = await h.new_core().run()
        assert len(h.source.archive_queries) == 3
        assert again.archive_walked is True and again.archive_pages == 0

    async def test_cap_mid_walk_saves_the_cursor_and_the_next_run_resumes(self):
        """Case 14: nothing downstream runs on a partial catalog."""
        h = Harness(archive_walked=False, cap=2)
        h.three_page_archive()
        h.behind_cutoff_market("H1")
        result = await h.core.run()
        assert result.capped is True
        assert result.archive_walked is False
        assert h.trades.cursor_log == ["archive-1"]
        assert h.trades.cursor == "archive-1"
        assert h.source.candle_queries == [] and h.source.trade_queries == []
        assert h.trades.state is None
        assert h.sink.types() == [T.PHASE_FINISHED]
        resumed = await h.new_core(cap=BIG_CAP).run()
        assert h.source.archive_queries[1]["cursor"] == "archive-1"
        assert resumed.archive_walked is True
        assert resumed.archive_pages == 2
        assert h.trades.cursor_log[-1] is None
        assert resumed.candle_markets_completed == 1
        assert resumed.floor_reached is True

    async def test_rejected_cursor_restarts_the_walk(self):
        """Case 15."""
        h = Harness(archive_walked=False)
        h.three_page_archive()
        await h.trades.set_cursor("archive-9")
        h.source.raise_on(
            "get_historical_markets", ProviderPermanentError("404 cursor"), at=1
        )
        result = await h.core.run()
        assert result.archive_restarted is True
        assert [q["cursor"] for q in h.source.archive_queries] == [
            "archive-9",
            None,
            "archive-1",
            "archive-2",
        ]
        assert result.archive_walked is True
        assert result.archive_pages == 3
        assert result.requests == 1 + 3 + h.parent_requests() + 3
        assert h.trades.cursor_log[-1] is None

    async def test_rejection_on_a_fresh_walk_is_an_abort(self):
        """Only a *saved* cursor restarts; a 4xx on page one propagates."""
        h = Harness(archive_walked=False)
        h.three_page_archive()
        h.source.raise_on("get_historical_markets", ProviderPermanentError("500"))
        with pytest.raises(ProviderPermanentError):
            await h.core.run()
        assert h.core.result.archive_restarted is False


class TestEventsAndTypes:
    async def test_exactly_one_historical_event_and_no_trades_event(self, h: Harness):
        """Case 10."""
        h.behind_cutoff_market("H1")
        await h.core.run()
        assert h.sink.types() == [T.PHASE_FINISHED]
        assert h.sink.phases() == ["historical"]
        event = h.sink.events[0]
        assert event.counts["windows_completed"] == 3
        assert event.counts["floor_reached"] == 1

    async def test_to_dict_matches_the_design_and_round_trips(self, h: Harness):
        """Case 11."""
        h.behind_cutoff_market("H1")
        result = await h.core.run()
        payload = result.to_dict()
        assert set(payload) == {
            "run_id",
            "started_at",
            "cap",
            "requests",
            "capped",
            "archive",
            "candles",
            "item_errors",
            "trades_row_missing",
            "floor",
            "watermark",
            "floor_reached",
            "windows_completed",
            "trades_fetched",
            "trades_written",
            "unknown_market",
            "excluded_by_rule",
            "duplicates",
            "unknown_prefixes",
            "duration_ms",
            "error",
        }
        assert payload["archive"]["walked"] is True
        assert payload["candles"]["markets_completed"] == 1
        assert payload["watermark"] == {
            "before": LIVE_FLOOR.isoformat(),
            "after": FLOOR.isoformat(),
        }
        assert payload["floor"] == FLOOR.isoformat()
        assert json.loads(json.dumps(payload)) == payload
        assert set(result.counts()) <= {
            k for k, v in payload.items() if isinstance(v, int | bool)
        } | {
            "archive_walked",
            "archive_pages",
            "archive_markets_written",
            "candle_markets_completed",
            "candle_requests",
            "candles_written",
            "candle_markets_remaining",
            "slow_markets",
            "item_errors",
        }

    @pytest.mark.parametrize(
        ("item_errors", "exc", "expected"),
        [
            ([], None, SyncOutcome.OK),
            ([CandleItemError("X", "404")], None, SyncOutcome.PARTIAL),
            (
                [CandleItemError("X", "404")],
                ProviderTransientError("503"),
                SyncOutcome.PROVIDER_ABORT,
            ),
            ([], errors.OperationalError("lost"), SyncOutcome.STORAGE_ABORT),
        ],
    )
    def test_classify(
        self,
        item_errors: list[CandleItemError],
        exc: Exception | None,
        expected: SyncOutcome,
    ):
        """Case 12."""
        result = HistoricalResult(uuid4(), NOW, cap=1, floor=FLOOR)
        result.item_errors.extend(item_errors)
        assert classify_historical(result, exc) is expected  # type: ignore[arg-type]

    def test_classify_refuses_an_unclassified_exception(self):
        result = HistoricalResult(uuid4(), NOW, cap=1, floor=FLOOR)
        with pytest.raises(TypeError):
            classify_historical(result, ValueError("x"))  # type: ignore[arg-type]

    async def test_adapters_refuse_the_calls_they_never_make(self):
        source = FakeHistoricalSource()
        with pytest.raises(NotImplementedError):
            await HistoricalTradeSource(source).get_historical_cutoff()
        catalog = HistoricalCatalogSource(source)
        with pytest.raises(NotImplementedError):
            await catalog.get_series_list()
        with pytest.raises(NotImplementedError):
            await catalog.get_historical_cutoff()

    async def test_catalog_adapter_counts_forwarded_requests(self):
        source = FakeHistoricalSource()
        source.catalog.add_series(make_series(SERIES))
        source.catalog.add_events(make_event(EVENT, SERIES))
        source.add_archive_page()
        adapter = HistoricalCatalogSource(source)
        await adapter.get_markets(limit=5, mve_filter=KALSHI_MVE_FILTER)
        await adapter.get_events(tickers=EVENT)
        await adapter.get_event(EVENT)
        await adapter.get_series(SERIES)
        assert adapter.requests == 4
        assert source.archive_queries == [
            {"cursor": None, "limit": 5, "mve_filter": KALSHI_MVE_FILTER}
        ]
