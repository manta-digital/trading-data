"""Sync core: run skeleton, page writer, series, markets walk, events refresh
(slice 262, Tasks 5.1–5.5)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from kalshi_support.fake_source import make_event, make_market, make_series
from kalshi_support.sync_harness import EVENT, NOW, SERIES, Harness
from psycopg import errors

from manta_trading.data.kalshi.constants import (
    CATALOG_WALK_FILTERS,
    KALSHI_MVE_FILTER,
    TICKERS_BATCH_SIZE,
    MarketStatus,
    MarketStatusFilter,
    Surface,
)
from manta_trading.data.kalshi.events import SyncEventType as T
from manta_trading.data.kalshi.sync import SyncOutcome, SyncPhase, classify, epoch


@pytest.fixture
def h() -> Harness:
    harness = Harness()
    harness.seed_parents()
    return harness


class TestRunSkeleton:
    async def test_event_sequence_on_empty_source(self):
        h = Harness()
        await h.core.run(settled_since=NOW - timedelta(hours=1))
        assert h.sink.types() == [
            T.RUN_STARTED,
            *([T.PHASE_FINISHED] * 5),
            T.RUN_FINISHED,
        ]
        assert h.sink.phases() == ["series", "markets", "events", "settled", "awaiting"]
        assert h.sink.events[-1].error is None

    async def test_result_is_json_serializable(self, h: Harness):
        h.live_market("M1")
        result = await h.core.run(settled_since=NOW - timedelta(hours=1))
        import json

        payload = json.loads(json.dumps(result.to_dict()))
        assert payload["phases"]["markets"]["written"] == 1
        assert payload["error"] is None


class TestPageWriter:
    async def test_integrity_error_rewrites_row_by_row(self, h: Harness):
        h.live_market("OK1")
        h.live_market("BAD", status="not-a-status")
        h.live_market("OK2")
        result = await h.core.run(settled_since=NOW - timedelta(hours=1))
        assert set(h.repo.markets) == {"OK1", "OK2"}
        assert [(e.ticker, e.phase) for e in result.item_errors] == [
            ("BAD", SyncPhase.MARKETS)
        ]
        assert "CheckViolation" in result.item_errors[0].reason
        item_events = [e for e in h.sink.events if e.event_type is T.ITEM_ERROR]
        assert [e.ticker for e in item_events] == ["BAD"]
        assert classify(result, None) is SyncOutcome.PARTIAL
        assert result.phases[SyncPhase.MARKETS].to_dict() == {
            "fetched": 3,
            "written": 2,
            "unchanged": 0,
            "skipped": 1,
        }
        # the rejected page, then the bad row in its own transaction
        assert h.repo.tx_log.count("rollback") == 2

    async def test_operational_error_propagates_after_run_finished(self, h: Harness):
        h.live_market("M1")
        h.repo.fail_on("upsert_markets", errors.OperationalError("connection lost"))
        with pytest.raises(errors.OperationalError):
            await h.core.run()
        assert h.sink.types()[-1] is T.RUN_FINISHED
        assert h.sink.events[-1].error == "OperationalError: connection lost"
        assert (
            classify(h.core.result, errors.OperationalError())
            is SyncOutcome.STORAGE_ABORT
        )

    async def test_programming_error_is_not_converted(self, h: Harness):
        h.live_market("M1")
        h.repo.fail_on("upsert_markets", errors.ProgrammingError("bad sql"))
        with pytest.raises(errors.ProgrammingError):
            await h.core.run()
        assert h.core.result.item_errors == []
        with pytest.raises(TypeError):
            classify(h.core.result, errors.ProgrammingError())  # type: ignore[arg-type]


class TestSeriesPhase:
    async def test_counts_and_event(self):
        h = Harness(load_fixtures=True)
        await h.core.run(settled_since=NOW - timedelta(hours=1))
        n = len(h.source.series)
        counts = h.core.result.phases[SyncPhase.SERIES]
        assert (counts.fetched, counts.written, counts.unchanged) == (n, n, 0)
        series_event = next(e for e in h.sink.events if e.phase == "series")
        assert (
            series_event.counts["fetched"] == n and series_event.counts["written"] == n
        )
        assert set(h.repo.series) == set(h.source.series)


class TestMarketsWalk:
    async def test_every_walk_query_is_non_mve_and_a_walk_filter(self, h: Harness):
        h.live_market("M1")
        await h.core.run(settled_since=NOW - timedelta(hours=1))
        walk = [q for q in h.source.markets_queries if q.get("status") is not None]
        assert [q["status"] for q in walk] == list(CATALOG_WALK_FILTERS)
        assert all(q["mve_filter"] == KALSHI_MVE_FILTER for q in walk)

    async def test_unknown_events_resolved_in_batches(self, h: Harness):
        for i in range(250):
            h.source.add_events(make_event(f"EV{i}", SERIES))
            h.live_market(f"M{i}", event=f"EV{i}")
        await h.core.run(settled_since=NOW - timedelta(hours=1))
        batches = [q for q in h.source.events_queries if q.get("tickers")]
        sizes = [len(str(q["tickers"]).split(",")) for q in batches]
        assert sizes == [100, 100, 50]
        assert max(sizes) <= TICKERS_BATCH_SIZE
        assert len(h.repo.markets) == 250 and len(h.repo.events) == 250

    async def test_unknown_series_fetched_once(self, h: Harness):
        h.source.add_series(make_series("SX"))
        h.source.add_events(make_event("EX1", "SX"), make_event("EX2", "SX"))
        # SX is not in the series list served by phase 1: hide it from the list
        # but keep it reachable by GET /series/{ticker}.
        listed = {t: s for t, s in h.source.series.items() if t != "SX"}
        h.source.series = {**listed, "SX": h.source.series["SX"]}
        h.source.get_series_list = _series_list_without(h.source, "SX")  # type: ignore[method-assign]
        h.live_market("M1", event="EX1")
        h.live_market("M2", event="EX2")
        await h.core.run(settled_since=NOW - timedelta(hours=1))
        assert h.source.series_requests == ["SX"]
        assert set(h.repo.markets) == {"M1", "M2"}

    async def test_parent_omitted_by_api_skips_dependents(self, h: Harness):
        h.live_market("GOOD")
        h.live_market("ORPHAN", event="GHOST")
        result = await h.core.run(settled_since=NOW - timedelta(hours=1))
        assert set(h.repo.markets) == {"GOOD"}
        assert [(e.ticker, e.phase) for e in result.item_errors] == [
            ("ORPHAN", SyncPhase.MARKETS)
        ]
        assert "GHOST" in result.item_errors[0].reason
        assert classify(result, None) is SyncOutcome.PARTIAL

    async def test_transitions_aggregate_and_seen(self, h: Harness):
        async with h.repo.transaction():
            await h.repo.upsert_series([make_series(SERIES)])
            await h.repo.upsert_events([make_event(EVENT, SERIES)])
            await h.repo.upsert_markets(
                [
                    make_market(
                        t, EVENT, status="active", result=None, settlement_ts=None
                    )
                    for t in ("A", "B", "C")
                ]
            )
        h.live_market("A", MarketStatusFilter.CLOSED, status=MarketStatus.CLOSED.value)
        h.live_market("B", MarketStatusFilter.CLOSED, status=MarketStatus.CLOSED.value)
        h.live_market(
            "C", MarketStatusFilter.PAUSED, status=MarketStatus.INACTIVE.value
        )
        h.live_market("D")
        result = await h.core.run(settled_since=NOW - timedelta(hours=1))
        assert result.transitions == {
            ("active", "closed"): 2,
            ("active", "inactive"): 1,
        }
        assert h.core.seen == {"A", "B", "C", "D"}
        markets_event = next(e for e in h.sink.events if e.phase == "markets")
        assert markets_event.transitions == {"active->closed": 2, "active->inactive": 1}


def _series_list_without(source, ticker: str):  # type: ignore[no-untyped-def]
    async def get_series_list():  # type: ignore[no-untyped-def]
        source.calls.append("get_series_list")
        return [s for t, s in source.series.items() if t != ticker]

    return get_series_list


class TestEventsRefresh:
    async def test_first_run_skips_then_second_run_uses_floor_and_cursor(
        self, h: Harness
    ):
        h.live_market("M1")
        await h.core.run(settled_since=NOW - timedelta(hours=1))
        refresh = [
            q for q in h.source.events_queries if q.get("min_updated_ts") is not None
        ]
        assert refresh == []
        state = await h.repo.get_sync_state(Surface.CATALOG)
        assert state is not None and state.last_full_sync_at == NOW

        h.source.page_size = 2
        for i in range(5):
            h.source.add_events(
                make_event(
                    f"UPD{i}", SERIES, last_updated_ts=NOW + timedelta(seconds=1)
                )
            )
        h.new_core()
        await h.core.run(settled_since=NOW - timedelta(hours=1))
        refresh = [
            q for q in h.source.events_queries if q.get("min_updated_ts") is not None
        ]
        assert [q["min_updated_ts"] for q in refresh] == [
            epoch(NOW - timedelta(seconds=1))
        ] * 3
        assert [q["cursor"] for q in refresh] == [None, "2", "4"]
        assert {f"UPD{i}" for i in range(5)} <= set(h.repo.events)
        assert h.core.result.phases[SyncPhase.EVENTS].fetched == 5
