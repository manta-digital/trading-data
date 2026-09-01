"""Unit tests for ``TradeSync`` (slice 265, Task 4.3b) and ``trade_types``.

Fake source, fake repository, recording sink — no network, no database.
The fake source records every window query; the fake repository records the
watermark in force at every write, so what is asserted here is the core's
logic: which windows are requested with which bounds, when the watermark
moves, how counts aggregate, and what the events and result carry.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from kalshi_support.fake_trade_repository import FakeTradeRepository
from kalshi_support.fake_trade_source import FakeTradeSource, make_trade
from kalshi_support.sync_harness import RecordingSink
from psycopg import errors

from manta_trading.data.kalshi import trade_sync as module
from manta_trading.data.kalshi.constants import (
    TRADE_LATE_ARRIVAL_GUARD,
    TRADE_PAGE_LIMIT,
    WINDOW_OVERLAP,
)
from manta_trading.data.kalshi.events import SyncEventType as T
from manta_trading.data.kalshi.selection import CollectionRule
from manta_trading.data.kalshi.sync_types import SyncOutcome, epoch
from manta_trading.data.kalshi.trade_repository import TradeState
from manta_trading.data.kalshi.trade_sync import PHASE, TradeSync
from manta_trading.data.kalshi.trade_types import (
    TradeResult,
    TradesBehindCutoffError,
    classify_trades,
)
from manta_trading.providers.errors import (
    ProviderPermanentError,
    ProviderTransientError,
)

NOW = datetime(2026, 8, 27, 14, 20, 11, tzinfo=UTC)
HOUR = timedelta(hours=1)
RULE = CollectionRule(True, frozenset(), frozenset({"Sports"}), None, None)


class Harness:
    def __init__(self, *, page_size: int | None = None) -> None:
        self.now = NOW
        self.source = FakeTradeSource(page_size=page_size)
        self.repo = FakeTradeRepository()
        self.repo.unknown_tickers.add("KXMVE-1")
        self.repo.excluded_tickers.add("SPORTS")
        self.sink = RecordingSink()
        self.run_id = uuid4()
        self.core = self.new_core()

    def new_core(self) -> TradeSync:
        self.sink = RecordingSink()
        self.core = TradeSync(
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
        return self.source.cutoff.trades_created_ts

    def catalog_walked(self, after_cutoff: timedelta) -> datetime:
        """The catalog walk started ``after_cutoff`` + the guard past the
        cutoff, so the pass bound is exactly ``cutoff + after_cutoff``."""
        walk_start = self.cutoff + after_cutoff + TRADE_LATE_ARRIVAL_GUARD
        self.repo.catalog_walk_start = walk_start
        return walk_start

    def tape(self, ticker: str, *offsets: timedelta) -> None:
        self.source.add_trades(
            *(make_trade(ticker, self.cutoff + offset) for offset in offsets)
        )

    def bounds(self) -> list[tuple[int, int]]:
        return [(q["min_ts"], q["max_ts"]) for q in self.source.trade_queries]  # type: ignore[misc]


@pytest.fixture
def h() -> Harness:
    return Harness()


class TestFirstRunAndBounds:
    async def test_first_run_initialises_both_instants_at_the_cutoff(self, h: Harness):
        """Case 1 (Criterion 6)."""
        result = await h.core.run()
        assert h.repo.state == TradeState(h.cutoff, h.cutoff)
        assert result.cutoff == h.cutoff
        assert result.coverage_from == h.cutoff
        assert result.watermark_before == h.cutoff
        assert result.watermark_after == h.cutoff

    async def test_windows_step_from_the_watermark_and_the_last_is_clamped(
        self, h: Harness
    ):
        """Case 2 (Criterion 7): every request's ``max_ts`` is its own
        window's end — three windows, the last one short."""
        h.catalog_walked(2 * HOUR + timedelta(minutes=30))
        result = await h.core.run()
        ends = [
            h.cutoff + HOUR,
            h.cutoff + 2 * HOUR,
            h.cutoff + 2 * HOUR + timedelta(minutes=30),
        ]
        assert [max_ts for _, max_ts in h.bounds()] == [epoch(end) for end in ends]
        assert result.windows_completed == 3
        assert result.requests == 3
        assert result.watermark_after == ends[-1]
        assert h.repo.state == TradeState(ends[-1], h.cutoff)

    async def test_lower_bound_steps_back_by_the_overlap(self, h: Harness):
        """Case 11 (Decision 1)."""
        h.catalog_walked(2 * HOUR)
        await h.core.run()
        starts = [h.cutoff, h.cutoff + HOUR]
        assert [min_ts for min_ts, _ in h.bounds()] == [
            epoch(start - WINDOW_OVERLAP) for start in starts
        ]
        assert all(q["limit"] == TRADE_PAGE_LIMIT for q in h.source.trade_queries)

    async def test_no_catalog_row_fetches_nothing_and_says_so(self, h: Harness):
        """Case 3."""
        result = await h.core.run()
        assert h.source.trade_queries == []
        assert result.catalog_missing is True
        assert result.requests == 0 and result.windows_completed == 0
        assert result.error is None
        assert h.repo.last_full_sync_at == NOW
        assert result.to_dict()["catalog_missing"] is True

    async def test_pass_bound_behind_the_watermark_fetches_nothing(self, h: Harness):
        h.repo.state = TradeState(h.cutoff + 5 * HOUR, h.cutoff)
        h.catalog_walked(HOUR)
        result = await h.core.run()
        assert h.source.trade_queries == []
        assert result.catalog_missing is False
        assert result.watermark_after == h.cutoff + 5 * HOUR


class TestCountsAndWatermark:
    async def test_page_counts_aggregate_and_the_identity_holds(self):
        """Case 4 (Criterion 2)."""
        h = Harness(page_size=2)
        h.catalog_walked(2 * HOUR)
        minute = timedelta(minutes=1)
        h.tape("POL", minute, 2 * minute, HOUR + minute)
        h.tape("SPORTS", 3 * minute, HOUR + 2 * minute)
        h.tape("KXMVE-1", 4 * minute)
        # A re-walked row: already stored before the phase.
        dup = make_trade("POL", h.cutoff + 5 * minute)
        h.source.add_trades(dup)
        h.repo.stored.add((dup.ticker, dup.created_time, dup.trade_id))
        result = await h.core.run()
        assert result.trades_fetched == 7
        assert result.trades_written == 3
        assert result.unknown_market == 1
        assert result.excluded_by_rule == 2
        assert result.duplicates == 1
        assert result.trades_fetched == (
            result.trades_written
            + result.unknown_market
            + result.excluded_by_rule
            + result.duplicates
        )
        # 5 rows in window 1 at 2 per page → 3 requests; 2 rows in window 2 → 1.
        assert result.requests == 4
        assert result.windows_completed == 2

    async def test_watermark_moves_only_after_a_windows_last_page(self):
        """Case 5."""
        h = Harness(page_size=1)
        h.catalog_walked(HOUR)
        h.tape("POL", timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=3))
        await h.core.run()
        assert len(h.repo.pages) == 3
        assert h.repo.watermark_at_write == [h.cutoff] * 3
        assert h.repo.state == TradeState(h.cutoff + HOUR, h.cutoff)
        # Each page and the watermark advance are their own transactions.
        assert h.repo.tx_log.count("commit") == 1 + 3 + 1 + 1  # init, pages, wm, sync


class TestAbortsAndCap:
    async def test_provider_error_mid_window_leaves_the_watermark(self):
        """Case 6 (Criterion 4): pages before the failure stay committed; the
        watermark does not move; the outcome is a provider abort."""
        h = Harness(page_size=1)
        h.catalog_walked(HOUR)
        h.tape("POL", timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=3))
        h.source.raise_on("get_trades", ProviderTransientError("503"), at=2)
        with pytest.raises(ProviderTransientError) as excinfo:
            await h.core.run()
        assert h.repo.state == TradeState(h.cutoff, h.cutoff)
        assert len(h.repo.stored) == 1
        assert h.core.result.error == "ProviderTransientError: 503"
        assert (
            classify_trades(h.core.result, excinfo.value) is SyncOutcome.PROVIDER_ABORT
        )
        assert h.sink.types() == [T.PHASE_FINISHED]

    async def test_operational_error_on_write_page_mid_window_leaves_the_watermark(
        self,
    ):
        """Case 12: case 6's twin for the other caught exception."""
        h = Harness(page_size=1)
        h.catalog_walked(HOUR)
        h.tape("POL", timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=3))
        h.repo.fail_on("write_page", errors.OperationalError("connection lost"), at=2)
        with pytest.raises(errors.OperationalError) as excinfo:
            await h.core.run()
        assert h.repo.state == TradeState(h.cutoff, h.cutoff)
        assert len(h.repo.stored) == 1
        assert h.repo.tx_log[-1] == "rollback"
        assert (
            classify_trades(h.core.result, excinfo.value) is SyncOutcome.STORAGE_ABORT
        )
        assert h.sink.types() == [T.PHASE_FINISHED]

    async def test_cap_stops_before_a_window_and_the_next_run_continues(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ):
        """Case 7 (Criterion 8)."""
        monkeypatch.setattr(module, "TRADE_REQUESTS_PER_PASS", 2)
        h.catalog_walked(5 * HOUR)
        result = await h.core.run()
        assert result.requests == 2
        assert result.windows_completed == 2
        assert result.capped is True
        assert h.repo.state == TradeState(h.cutoff + 2 * HOUR, h.cutoff)
        assert result.watermark_after == h.cutoff + 2 * HOUR
        again = await h.new_core().run()
        assert again.watermark_before == h.cutoff + 2 * HOUR
        assert h.bounds()[2][0] == epoch(h.cutoff + 2 * HOUR - WINDOW_OVERLAP)
        assert again.capped is True and again.windows_completed == 2

    async def test_uncapped_run_reports_capped_false(self, h: Harness):
        h.catalog_walked(HOUR)
        assert (await h.core.run()).capped is False

    async def test_watermark_behind_the_cutoff_raises_naming_the_range(
        self, h: Harness
    ):
        """Case 8 (Criterion 6): nothing jumps forward."""
        behind = h.cutoff - timedelta(days=2)
        h.repo.state = TradeState(behind, behind)
        h.catalog_walked(HOUR)
        with pytest.raises(TradesBehindCutoffError) as excinfo:
            await h.core.run()
        message = str(excinfo.value)
        assert behind.isoformat() in message and h.cutoff.isoformat() in message
        assert "historical phase" in message
        assert h.repo.state == TradeState(behind, behind)
        assert h.source.trade_queries == []
        assert h.core.result.error is not None
        assert h.sink.types() == [T.PHASE_FINISHED]


class TestEventsAndLogs:
    async def test_phase_finished_once_with_the_trades_phase(self, h: Harness):
        """Case 9."""
        h.catalog_walked(HOUR)
        h.tape("POL", timedelta(minutes=1))
        result = await h.core.run()
        assert h.sink.types() == [T.PHASE_FINISHED]
        assert h.sink.phases() == [PHASE] == ["trades"]
        finished = h.sink.events[0]
        assert finished.run_id == h.run_id
        assert finished.counts == result.counts()
        assert finished.counts["trades_written"] == 1
        assert finished.error is None

    async def test_unknown_prefix_tally_groups_and_logs_once(
        self, h: Harness, caplog: pytest.LogCaptureFixture
    ):
        """Case 10 (Criterion 9): display only, one line per phase."""
        h.repo.unknown_tickers |= {"KXMVE-2", "KXOTHER-1"}
        h.catalog_walked(HOUR)
        h.tape("KXMVE-1", timedelta(minutes=1), timedelta(minutes=2))
        h.tape("KXMVE-2", timedelta(minutes=3))
        h.tape("KXOTHER-1", timedelta(minutes=4))
        with caplog.at_level(logging.INFO, logger=module.__name__):
            result = await h.core.run()
        assert result.unknown_prefixes == {"KXMVE": 3, "KXOTHER": 1}
        lines = [
            r.getMessage()
            for r in caplog.records
            if "unknown markets" in r.getMessage()
        ]
        assert lines == ["trades unknown markets: KXMVE 3 · KXOTHER 1"]

    async def test_no_unknown_line_when_nothing_is_unknown(
        self, h: Harness, caplog: pytest.LogCaptureFixture
    ):
        h.catalog_walked(HOUR)
        with caplog.at_level(logging.INFO, logger=module.__name__):
            await h.core.run()
        assert not [r for r in caplog.records if "unknown markets" in r.getMessage()]

    async def test_start_and_window_lines(
        self, h: Harness, caplog: pytest.LogCaptureFixture
    ):
        h.catalog_walked(HOUR)
        h.tape("POL", timedelta(minutes=1))
        with caplog.at_level(logging.INFO, logger=module.__name__):
            await h.core.run()
        messages = [r.getMessage() for r in caplog.records]
        start = next(m for m in messages if m.startswith("kalshi trades phase started"))
        assert f"cutoff={h.cutoff.isoformat()}" in start
        assert f"watermark={h.cutoff.isoformat()}" in start
        assert RULE.describe() in start
        window = next(m for m in messages if m.startswith("trades window"))
        assert window == (
            f"trades window {h.cutoff.isoformat()}→{(h.cutoff + HOUR).isoformat()} "
            "pages 1 fetched 1 written 1 unknown 0 excluded 0"
        )


class TestTypes:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (None, SyncOutcome.OK),
            (ProviderTransientError("503"), SyncOutcome.PROVIDER_ABORT),
            (ProviderPermanentError("400"), SyncOutcome.PROVIDER_ABORT),
            (errors.OperationalError("lost"), SyncOutcome.STORAGE_ABORT),
        ],
    )
    def test_classify_never_partial(self, exc: Exception | None, expected: SyncOutcome):
        result = TradeResult(run_id=uuid4(), started_at=NOW)
        assert classify_trades(result, exc) is expected  # type: ignore[arg-type]
        assert classify_trades(result, exc) is not SyncOutcome.PARTIAL  # type: ignore[arg-type]

    def test_classify_refuses_an_unclassified_exception(self):
        result = TradeResult(run_id=uuid4(), started_at=NOW)
        with pytest.raises(TypeError):
            classify_trades(result, ValueError("x"))  # type: ignore[arg-type]

    async def test_to_dict_matches_the_design_payload_and_round_trips(self, h: Harness):
        h.catalog_walked(HOUR)
        h.tape("POL", timedelta(minutes=1))
        h.tape("KXMVE-1", timedelta(minutes=2))
        result = await h.core.run()
        payload = json.loads(json.dumps(result.to_dict()))
        assert set(payload) == {
            "run_id",
            "started_at",
            "cutoff",
            "coverage_from",
            "watermark",
            "windows_completed",
            "requests",
            "capped",
            "catalog_missing",
            "trades_fetched",
            "trades_written",
            "unknown_market",
            "excluded_by_rule",
            "duplicates",
            "unknown_prefixes",
            "duration_ms",
            "error",
        }
        assert payload["watermark"] == {
            "before": h.cutoff.isoformat(),
            "after": (h.cutoff + HOUR).isoformat(),
        }
        assert payload["unknown_prefixes"] == {"KXMVE": 1}
        assert payload["capped"] is False and payload["error"] is None
        assert payload["run_id"] == str(h.run_id)
