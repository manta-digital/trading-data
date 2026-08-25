"""Sync core: awaiting reconciliation, state, classification (slice 262,
Tasks 5.7–5.8)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from manta_trading.data.kalshi.constants import (
    KALSHI_MVE_FILTER,
    MarketStatusFilter,
    Surface,
)
from manta_trading.data.kalshi.events import SyncEvent
from manta_trading.data.kalshi.events import SyncEventType as T
from manta_trading.data.kalshi.sync import SyncOutcome, classify
from manta_trading.providers.errors import (
    ProviderPermanentError,
    ProviderTransientError,
)

from ._fake_source import make_market
from ._sync_harness import EVENT, NOW, Harness

SINCE = NOW - timedelta(hours=1)


@pytest.fixture
def h() -> Harness:
    harness = Harness()
    harness.seed_parents()
    return harness


async def enter_one(h: Harness, ticker: str = "PAST") -> None:
    """Run 1: walk a market whose close has passed → it enters the set."""
    h.live_market(ticker, close_time=NOW - timedelta(hours=2))
    result = await h.core.run(settled_since=SINCE)
    assert result.awaiting_entered == 1
    assert list(h.repo.awaiting) == [ticker]
    h.source.live[MarketStatusFilter.OPEN].clear()


class TestAwaiting:
    async def test_walked_closed_market_enters(self, h: Harness):
        await enter_one(h)
        assert h.repo.awaiting["PAST"].last_checked_at is None

    async def test_settled_capture_retires(self, h: Harness):
        await enter_one(h)
        h.settled_market("PAST", NOW - timedelta(minutes=30))
        h.new_core()
        result = await h.core.run(settled_since=SINCE)
        assert result.awaiting_retired == 1 and result.awaiting_checked == 0
        assert h.repo.awaiting == {}
        assert not [q for q in h.source.markets_queries if q.get("tickers")]

    async def test_vanished_is_looked_up_by_ticker(self, h: Harness):
        await enter_one(h)
        h.source.add_lookup(
            make_market(
                "PAST",
                EVENT,
                status="closed",
                result=None,
                settlement_ts=None,
                close_time=NOW - timedelta(hours=2),
            )
        )
        h.new_core()
        result = await h.core.run(settled_since=SINCE)
        lookups = [q for q in h.source.markets_queries if q.get("tickers")]
        assert [q["tickers"] for q in lookups] == ["PAST"]
        assert all(q["mve_filter"] == KALSHI_MVE_FILTER for q in lookups)
        assert result.awaiting_checked == 1 and result.awaiting_unreachable == 0
        assert h.repo.awaiting["PAST"].last_checked_at == NOW
        assert h.repo.markets["PAST"].status == "closed"
        assert h.core.result.transitions == {("active", "closed"): 1}

    async def test_omitted_ticker_is_unreachable_and_stays(self, h: Harness):
        await enter_one(h)
        h.source.lookup.clear()
        h.new_core()
        result = await h.core.run(settled_since=SINCE)
        assert result.awaiting_unreachable == 1 and result.awaiting_checked == 1
        assert result.item_errors == []
        assert list(h.repo.awaiting) == ["PAST"]
        assert h.repo.awaiting["PAST"].last_checked_at == NOW
        assert classify(result, None) is SyncOutcome.OK

    async def test_lookup_that_returns_finalized_retires(self, h: Harness):
        await enter_one(h)
        h.source.add_lookup(
            make_market(
                "PAST",
                EVENT,
                status="finalized",
                result="no",
                close_time=NOW - timedelta(hours=2),
                settlement_ts=NOW - timedelta(minutes=5),
            )
        )
        h.new_core()
        result = await h.core.run(settled_since=SINCE)
        assert result.awaiting_retired == 1
        assert h.repo.awaiting == {}

    async def test_sink_failure_never_aborts(self, h: Harness):
        class BrokenSink:
            def emit(self, event: SyncEvent) -> None:
                raise RuntimeError("sink down")

        h.live_market("M1")
        h.core._sink = BrokenSink()  # type: ignore[assignment]
        result = await h.core.run(settled_since=SINCE)
        assert result.error is None and classify(result, None) is SyncOutcome.OK


class TestStateAndClassification:
    async def test_last_full_sync_written_only_after_phase_five(self, h: Harness):
        h.live_market("M1")
        await h.core.run(settled_since=SINCE)
        state = await h.repo.get_sync_state(Surface.CATALOG)
        assert state is not None and state.last_full_sync_at == NOW
        order = [m for m, _ in h.repo.writes]
        assert order.index("set_last_full_sync") > order.index("mark_checked")

    async def test_provider_abort_in_walk_leaves_state_untouched(self, h: Harness):
        h.live_market("M1")
        h.source.raise_on(
            "get_markets",
            ProviderTransientError("503"),
            when=lambda q: q.get("status") is MarketStatusFilter.PAUSED,
        )
        with pytest.raises(ProviderTransientError):
            await h.core.run(settled_since=SINCE)
        assert await h.repo.get_sync_state(Surface.CATALOG) is None
        assert h.sink.types()[-1] is T.RUN_FINISHED
        assert h.sink.phases() == ["series"]
        assert set(h.repo.markets) == {"M1"}, "the open walk committed before the abort"
        assert (
            classify(h.core.result, ProviderTransientError())
            is SyncOutcome.PROVIDER_ABORT
        )

    async def test_classification_table(self, h: Harness):
        result = h.core.result
        assert classify(result, None) is SyncOutcome.OK
        assert classify(result, ProviderPermanentError()) is SyncOutcome.PROVIDER_ABORT
        assert classify(result, ProviderTransientError()) is SyncOutcome.PROVIDER_ABORT
        from psycopg import errors

        assert classify(result, errors.QueryCanceled()) is SyncOutcome.STORAGE_ABORT
        h.core.item_error(list(result.phases)[0], "X", "why")
        assert classify(result, None) is SyncOutcome.PARTIAL
        assert classify(result, ProviderTransientError()) is SyncOutcome.PROVIDER_ABORT

    async def test_every_markets_query_of_a_full_run_excludes_mve(self, h: Harness):
        await enter_one(h)
        h.source.add_lookup(
            make_market(
                "PAST",
                EVENT,
                status="closed",
                result=None,
                settlement_ts=None,
                close_time=NOW - timedelta(hours=2),
            )
        )
        h.settled_market("S1", NOW - timedelta(minutes=30))
        h.live_market("LIVE")
        h.new_core()
        await h.core.run(settled_since=NOW - timedelta(hours=8))
        kinds = {
            "walk" if q.get("status") else "lookup" if q.get("tickers") else "window"
            for q in h.source.markets_queries
        }
        assert kinds == {"walk", "lookup", "window"}
        assert h.source.markets_queries, "the run issued markets requests"
        assert all(
            q["mve_filter"] == KALSHI_MVE_FILTER for q in h.source.markets_queries
        )
