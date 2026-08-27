"""Unit tests for ``CandleSync`` (slice 264, Task 5.3b).

Fake source, fake repository, recording sink — no network, no database.
The fake source records every batch query; the fake repository mirrors the
real statements' conditions, so what is asserted here is the core's logic:
which sets are requested, how the response advances state, what becomes an
item error, and what the events and result carry.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from kalshi_support.fake_candle_repository import FakeCandleRepository, FakeMarket
from kalshi_support.fake_candle_source import (
    FakeCandleSource,
    make_candle,
    make_trade_candle,
)
from kalshi_support.sync_harness import RecordingSink
from psycopg import errors

from manta_trading.data.kalshi import candle_sync as module
from manta_trading.data.kalshi.candle_plan import last_complete_period
from manta_trading.data.kalshi.candle_sync import NOT_SERVED, CandleSync
from manta_trading.data.kalshi.candle_types import (
    CandleResult,
    CandleRule,
    classify_candles,
)
from manta_trading.data.kalshi.constants import (
    CANDLE_BATCH_MAX_TICKERS,
    CANDLE_FIRST_SIGHT_LOOKBACK,
    COLLECTED_CANDLE_PERIOD,
    MarketStatus,
)
from manta_trading.data.kalshi.events import SyncEventType as T
from manta_trading.data.kalshi.sync_types import SyncOutcome
from manta_trading.providers.errors import ProviderPermanentError

PERIOD = COLLECTED_CANDLE_PERIOD
MINUTE = timedelta(minutes=1)
NOW = datetime(2026, 8, 27, 14, 20, 11, tzinfo=UTC)
LAST_COMPLETE = last_complete_period(NOW, PERIOD)
RULE = CandleRule(True, frozenset(), frozenset({"Sports"}), None, None)


class Harness:
    def __init__(self, *, now: datetime = NOW) -> None:
        self.now = now
        self.source = FakeCandleSource()
        self.repo = FakeCandleRepository()
        self.sink = RecordingSink()
        self.run_id = uuid4()
        self.core = self.new_core()

    def new_core(self) -> CandleSync:
        self.sink = RecordingSink()
        self.core = CandleSync(
            self.source,
            self.repo,
            self.sink,
            rule=RULE,
            run_id=self.run_id,
            clock=lambda: self.now,
        )
        return self.core

    @property
    def cutoff(self) -> datetime:
        return self.source.cutoff.market_settled_ts

    def live(
        self, ticker: str, *, opened: timedelta = timedelta(hours=3)
    ) -> FakeMarket:
        return self.repo.add_market(
            FakeMarket(ticker, self.now - opened, self.now + timedelta(days=1))
        )

    def finalized(self, ticker: str, settled: datetime, **kw: object) -> FakeMarket:
        return self.repo.add_market(
            FakeMarket(
                ticker,
                settled - timedelta(hours=1),
                settled - MINUTE,
                status=MarketStatus.FINALIZED.value,
                settlement_ts=settled,
                **kw,  # type: ignore[arg-type]
            )
        )

    def queried(self) -> list[tuple[str, ...]]:
        return [q["tickers"] for q in self.source.candle_queries]  # type: ignore[misc]


@pytest.fixture
def h() -> Harness:
    return Harness()


class TestEmptyPending:
    async def test_completes_writes_state_and_emits_zero_counts(self, h: Harness):
        result = await h.core.run()
        assert h.source.candle_queries == []
        assert h.repo.sync_state == (NOW, h.cutoff)
        assert result.cutoff == h.cutoff
        assert result.requests == 0 and result.error is None
        finished = h.sink.events[-1]
        assert finished.event_type is T.PHASE_FINISHED
        assert finished.phase == "candles"
        assert finished.counts["requests"] == 0
        assert finished.counts["backlog_remaining"] == 0
        assert h.sink.types() == [T.PHASE_FINISHED]


class TestPendingSets:
    async def test_live_finishing_and_backlog_are_all_requested(self, h: Harness):
        h.live("LIVE")
        done = h.finalized("FIN", h.now - timedelta(hours=2))
        await h.repo.advance_state(
            PERIOD,
            [module.StateAdvance("FIN", done.close_time - MINUTE, done.open_time)],
        )
        h.finalized("BACK", h.cutoff + timedelta(days=1))
        result = await h.core.run()
        requested = {t for q in h.queried() for t in q}
        assert requested == {"LIVE", "FIN", "BACK"}
        assert (
            result.pending_live,
            result.pending_finishing,
            result.pending_backlog,
        ) == (
            1,
            1,
            1,
        )

    async def test_only_the_backlog_is_capped(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(module, "BACKLOG_ROWS_PER_PASS", 2)
        for i in range(3):
            h.live(f"L{i}")
            h.finalized(f"B{i}", h.cutoff + timedelta(days=i + 1))
        result = await h.core.run()
        requested = {t for q in h.queried() for t in q}
        assert {"L0", "L1", "L2"} <= requested
        # Oldest settlement first; B2 waits for the next pass.
        assert requested & {"B0", "B1", "B2"} == {"B0", "B1"}
        assert result.pending_backlog == 2
        # Criterion 8: the remainder is the full count, not the capped rows.
        assert result.backlog_remaining == 1

    async def test_deselected_and_behind_cutoff_markets_are_not_requested(
        self, h: Harness
    ):
        h.live("SPORTS").selected_recent = False
        h.finalized("OLD", h.cutoff - timedelta(days=1))
        result = await h.core.run()
        assert h.source.candle_queries == []
        assert result.behind_cutoff == 1

    async def test_first_sight_window_and_watermark_start(self, h: Harness):
        """Decision 5 at the core: the target start becomes ``coverage_from_ts``
        (seen young → open; seen old → lookback; a state row → its watermark
        moves and its coverage stays). The request window is the batch's
        union, so the earliest start is what the endpoint is asked for."""
        young = h.live("YOUNG", opened=timedelta(hours=3))
        old = h.live("OLD", opened=timedelta(days=40))
        seen = h.live("SEEN", opened=timedelta(days=40))
        mark = h.now - timedelta(hours=2)
        await h.repo.advance_state(
            PERIOD, [module.StateAdvance("SEEN", mark, seen.open_time)]
        )
        await h.core.run()
        coverage = {t: row.coverage_from_ts for (t, _), row in h.repo.state.items()}
        assert coverage["YOUNG"] == young.open_time
        assert coverage["OLD"] == h.now - CANDLE_FIRST_SIGHT_LOOKBACK
        assert coverage["SEEN"] == seen.open_time
        assert old.open_time < coverage["OLD"]
        assert all(r.watermark_ts == LAST_COMPLETE for r in h.repo.state.values())
        [query] = h.source.candle_queries
        assert query["start_ts"] == int(coverage["OLD"].timestamp())


class TestBatchWrite:
    async def test_candles_written_and_state_advanced(self, h: Harness):
        h.live("M1")
        h.source.add_candles(
            "M1", make_candle(LAST_COMPLETE - MINUTE), make_trade_candle(LAST_COMPLETE)
        )
        result = await h.core.run()
        assert result.candles_fetched == 2 and result.candles_written == 2
        assert result.markets_requested == 1 and result.markets_advanced == 1
        assert h.repo.state[("M1", int(PERIOD))].watermark_ts == LAST_COMPLETE
        assert h.repo.tx_log.count("commit") == 2  # the batch, then sync_state

    async def test_present_with_zero_candles_still_advances(self, h: Harness):
        market = h.live("IDLE")
        result = await h.core.run()
        assert result.candles_fetched == 0
        assert result.markets_advanced == 1
        state = h.repo.state[("IDLE", int(PERIOD))]
        assert state.watermark_ts == LAST_COMPLETE
        assert state.coverage_from_ts == market.open_time
        # A second pass with nothing new asks for nothing.
        h.new_core()
        await h.core.run()
        assert len(h.source.candle_queries) == 1

    async def test_omitted_ticker_is_an_item_error_with_no_advance(self, h: Harness):
        h.live("GONE")
        h.live("OK")
        h.source.omit.add("GONE")
        result = await h.core.run()
        assert [e.to_dict() for e in result.item_errors] == [
            {"ticker": "GONE", "reason": NOT_SERVED}
        ]
        assert ("GONE", int(PERIOD)) not in h.repo.state
        assert ("OK", int(PERIOD)) in h.repo.state
        assert result.markets_requested == 2 and result.markets_advanced == 1
        item = next(e for e in h.sink.events if e.event_type is T.ITEM_ERROR)
        assert item.phase == "candles" and item.ticker == "GONE"
        assert item.run_id == h.run_id

    async def test_closed_market_watermark_clamps_to_close_plus_period(
        self, h: Harness
    ):
        settled = h.now - timedelta(hours=2)
        market = h.finalized("FIN", settled)
        await h.core.run()
        state = h.repo.state[("FIN", int(PERIOD))]
        assert state.watermark_ts == market.close_time + MINUTE
        assert state.coverage_from_ts == market.open_time

    async def test_coverage_is_set_once_and_never_moved(self, h: Harness):
        market = h.live("M1", opened=timedelta(days=40))
        await h.core.run()
        first = h.repo.state[("M1", int(PERIOD))].coverage_from_ts
        assert first == h.now - CANDLE_FIRST_SIGHT_LOOKBACK
        h.now += timedelta(hours=1)
        h.new_core()
        await h.core.run()
        assert h.repo.state[("M1", int(PERIOD))].coverage_from_ts == first
        assert h.repo.state[("M1", int(PERIOD))].watermark_ts == last_complete_period(
            h.now, PERIOD
        )
        assert market.open_time < first

    async def test_duplicate_candles_on_rerun_write_nothing(self, h: Harness):
        h.live("M1")
        h.source.add_candles("M1", make_candle(LAST_COMPLETE - MINUTE))
        await h.core.run()
        # Force a re-request of the same window by rolling the watermark back.
        h.repo.state[("M1", int(PERIOD))].watermark_ts = LAST_COMPLETE - MINUTE * 5
        h.new_core()
        result = await h.core.run()
        assert result.candles_fetched == 1 and result.candles_written == 0


class TestFailures:
    async def test_provider_error_aborts_and_later_batches_are_not_requested(
        self, h: Harness
    ):
        for i in range(CANDLE_BATCH_MAX_TICKERS + 5):
            h.live(f"M{i:03}")
        h.source.raise_on(
            "get_markets_candlesticks", ProviderPermanentError("400 cap"), at=1
        )
        with pytest.raises(ProviderPermanentError):
            await h.core.run()
        assert len(h.source.candle_queries) == 1
        assert h.repo.sync_state is None
        assert h.core.result.error is not None and "400 cap" in h.core.result.error
        finished = h.sink.events[-1]
        assert finished.event_type is T.PHASE_FINISHED
        assert finished.error == h.core.result.error

    async def test_operational_error_propagates_and_rolls_the_batch_back(
        self, h: Harness
    ):
        h.live("M1")
        h.repo.fail_on("advance_state", errors.OperationalError("connection lost"))
        with pytest.raises(errors.OperationalError):
            await h.core.run()
        assert h.repo.tx_log[-1] == "rollback"
        assert h.repo.candles == {}

    async def test_integrity_error_is_retried_per_market(self, h: Harness):
        h.live("A")
        h.live("B")
        h.source.add_candles("A", make_candle(LAST_COMPLETE))
        h.source.add_candles("B", make_candle(LAST_COMPLETE))
        # The batch fails; on the per-market retry only B fails again.
        h.repo.fail_on("insert_candles", errors.ForeignKeyViolation("batch"), at=1)
        h.repo.fail_on("insert_candles", errors.ForeignKeyViolation("B"), at=3)
        result = await h.core.run()
        assert [e.ticker for e in result.item_errors] == ["B"]
        assert ("A", int(PERIOD)) in h.repo.state
        assert ("B", int(PERIOD)) not in h.repo.state
        assert result.candles_written == 1
        assert classify_candles(result, None) is SyncOutcome.PARTIAL


class TestClassify:
    def test_each_outcome(self):
        result = CandleResult(run_id=uuid4(), started_at=NOW, period=PERIOD)
        assert classify_candles(result, None) is SyncOutcome.OK
        result.item_errors.append(module.CandleItemError("X", NOT_SERVED))
        assert classify_candles(result, None) is SyncOutcome.PARTIAL
        assert (
            classify_candles(result, ProviderPermanentError("x"))
            is SyncOutcome.PROVIDER_ABORT
        )
        assert (
            classify_candles(result, errors.OperationalError("x"))
            is SyncOutcome.STORAGE_ABORT
        )
        with pytest.raises(TypeError):
            classify_candles(result, RuntimeError("bug"))  # type: ignore[arg-type]


class TestResultAndEvents:
    async def test_to_dict_round_trips_with_item_errors_and_cutoff(self, h: Harness):
        h.live("GONE")
        h.source.omit.add("GONE")
        result = await h.core.run()
        payload = json.loads(json.dumps(result.to_dict()))
        assert set(payload) == {
            "run_id",
            "started_at",
            "period",
            "cutoff",
            "pending",
            "requests",
            "markets_requested",
            "markets_advanced",
            "candles_fetched",
            "candles_written",
            "item_errors",
            "duration_ms",
            "error",
        }
        assert set(payload["pending"]) == {
            "live",
            "finishing",
            "backlog",
            "backlog_remaining",
        }
        assert payload["cutoff"] == h.cutoff.isoformat()
        assert payload["item_errors"] == [{"ticker": "GONE", "reason": NOT_SERVED}]
        assert payload["period"] == 1

    async def test_events_carry_phase_and_run_id(self, h: Harness):
        h.live("M1")
        await h.core.run()
        assert {e.phase for e in h.sink.events} == {"candles"}
        assert {e.run_id for e in h.sink.events} == {h.run_id}
        finished = h.sink.events[-1]
        assert finished.counts["markets_advanced"] == 1
        assert finished.duration_ms is not None

    async def test_start_line_carries_cutoff_and_rule(
        self, h: Harness, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.INFO, logger=module.__name__):
            await h.core.run()
        line = next(r.message for r in caplog.records if "phase started" in r.message)
        assert h.cutoff.isoformat() in line
        assert RULE.describe() in line

    async def test_progress_line_at_the_configured_cadence(
        self,
        h: Harness,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(module, "CANDLE_PROGRESS_EVERY_REQUESTS", 2)
        # 50-minute windows: 100 tickers × 51 periods fit one request, so
        # 300 markets plan to exactly three batches.
        for i in range(CANDLE_BATCH_MAX_TICKERS * 3):
            h.live(f"M{i:03}", opened=timedelta(minutes=50))
        with caplog.at_level(logging.INFO, logger=module.__name__):
            await h.core.run()
        progress = [r.message for r in caplog.records if "progress" in r.message]
        assert h.core.result.requests == 3
        assert len(progress) == 1
        assert "requests=2/3" in progress[0]
