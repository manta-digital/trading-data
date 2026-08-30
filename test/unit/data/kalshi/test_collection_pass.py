"""Unit tests for the collection pass (slice 263, Tasks 2.3 and 3.3).

Two subjects: the sequencing and aggregation rules with scripted fake phases
(no I/O at all), and the real :class:`CatalogPhase` driven over
``kalshi_support``'s fake source and fake repository.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from kalshi_support.fake_candle_repository import FakeCandleRepository, FakeMarket
from kalshi_support.sync_harness import NOW, Harness, RecordingSink
from psycopg import errors

from manta_trading.data.kalshi.collection_pass import (
    PASS_PHASES,
    SKIPPED,
    CandlesPhase,
    CatalogPhase,
    CollectionPass,
    PassPhaseName,
    PassResult,
    PhaseReport,
    classify_pass,
)
from manta_trading.data.kalshi.constants import Surface
from manta_trading.data.kalshi.events import SyncEventType as T
from manta_trading.data.kalshi.run_context import KalshiRun
from manta_trading.data.kalshi.selection import CollectionRule
from manta_trading.data.kalshi.sync_types import SyncOutcome
from manta_trading.providers.errors import ProviderTransientError

if TYPE_CHECKING:
    from psycopg import AsyncConnection

    from manta_trading.data.kalshi.client import KalshiClient

OUTCOMES = list(SyncOutcome)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePhase:
    """A phase that records its call and returns a scripted report or raises."""

    def __init__(
        self,
        name: PassPhaseName,
        outcome: SyncOutcome = SyncOutcome.OK,
        *,
        raise_with: BaseException | None = None,
        order: list[PassPhaseName] | None = None,
    ) -> None:
        self.name = name
        self._outcome = outcome
        self._raise_with = raise_with
        self._order = order if order is not None else []
        self.calls = 0

    async def run(self, run: KalshiRun) -> PhaseReport:
        self.calls += 1
        self._order.append(self.name)
        if self._raise_with is not None:
            raise self._raise_with
        return PhaseReport(
            name=self.name,
            outcome=self._outcome,
            summary={"seen": True},
            duration_ms=5,
            error="boom" if self._outcome is not SyncOutcome.OK else None,
        )


def _run(sink: RecordingSink | None = None) -> KalshiRun:
    client = MagicMock()
    client.mode = "public"
    client.rate_limit.requests_per_minute = 300
    return KalshiRun(
        settings=MagicMock(),
        client=client,
        conn=MagicMock(),
        sink=sink if sink is not None else RecordingSink(),
        run_id=uuid4(),
        clock=lambda: NOW,
    )


def _report(outcome: SyncOutcome | Any, name: PassPhaseName = PassPhaseName.CATALOG):
    return PhaseReport(name=name, outcome=outcome, summary={}, duration_ms=0)


# ---------------------------------------------------------------------------
# classify_pass (Criterion 3)
# ---------------------------------------------------------------------------


class TestClassifyPass:
    def test_empty_is_ok(self):
        assert classify_pass([]) is SyncOutcome.OK

    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_single_report_is_its_own_outcome(self, outcome: SyncOutcome):
        assert classify_pass([_report(outcome)]) is outcome

    @pytest.mark.parametrize(("first", "second"), itertools.product(OUTCOMES, OUTCOMES))
    def test_every_ordered_pair_takes_the_worst(
        self, first: SyncOutcome, second: SyncOutcome
    ):
        precedence = [
            SyncOutcome.STORAGE_ABORT,
            SyncOutcome.PROVIDER_ABORT,
            SyncOutcome.PARTIAL,
            SyncOutcome.OK,
        ]
        expected = min((first, second), key=precedence.index)
        assert classify_pass([_report(first), _report(second)]) is expected

    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_skipped_never_influences_the_result(self, outcome: SyncOutcome):
        reports = [_report(outcome), _report(SKIPPED)]
        assert classify_pass(reports) is classify_pass([_report(outcome)])

    def test_all_skipped_is_ok(self):
        assert classify_pass([_report(SKIPPED), _report(SKIPPED)]) is SyncOutcome.OK

    def test_skipped_is_not_a_sync_outcome(self):
        assert SKIPPED not in set(SyncOutcome)


# ---------------------------------------------------------------------------
# Sequencing (Criterion 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSequencing:
    async def test_runs_in_tuple_order(self):
        order: list[PassPhaseName] = []
        phases = [
            FakePhase(PassPhaseName.CATALOG, order=order),
            FakePhase(PassPhaseName.CATALOG, order=order),
        ]
        result = await CollectionPass(_run(), phases).run()
        assert order == [PassPhaseName.CATALOG, PassPhaseName.CATALOG]
        assert result.outcome is SyncOutcome.OK
        assert [r.outcome for r in result.reports] == [SyncOutcome.OK, SyncOutcome.OK]

    @pytest.mark.parametrize(
        "abort", [SyncOutcome.PROVIDER_ABORT, SyncOutcome.STORAGE_ABORT]
    )
    async def test_abort_skips_the_remainder(self, abort: SyncOutcome):
        first = FakePhase(PassPhaseName.CATALOG, abort)
        second = FakePhase(PassPhaseName.CATALOG)
        result = await CollectionPass(_run(), [first, second]).run()
        assert second.calls == 0
        assert result.outcome is abort
        skipped = result.reports[1]
        assert skipped.outcome == SKIPPED
        assert skipped.duration_ms == 0 and skipped.summary == {}

    async def test_partial_does_not_stop_the_pass(self):
        first = FakePhase(PassPhaseName.CATALOG, SyncOutcome.PARTIAL)
        second = FakePhase(PassPhaseName.CATALOG)
        result = await CollectionPass(_run(), [first, second]).run()
        assert second.calls == 1
        assert result.outcome is SyncOutcome.PARTIAL

    async def test_unclassified_exception_propagates(self):
        first = FakePhase(PassPhaseName.CATALOG, raise_with=RuntimeError("bug"))
        second = FakePhase(PassPhaseName.CATALOG)
        with pytest.raises(RuntimeError, match="bug"):
            await CollectionPass(_run(), [first, second]).run()
        assert second.calls == 0


# ---------------------------------------------------------------------------
# Events and result shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPassEvents:
    async def test_brackets_the_pass_once_each_with_one_run_id(self):
        sink = RecordingSink()
        run = _run(sink)
        await CollectionPass(run, [FakePhase(PassPhaseName.CATALOG)]).run()
        assert sink.types() == [T.PASS_STARTED, T.PASS_FINISHED]
        assert {e.run_id for e in sink.events} == {run.run_id}
        assert all(e.phase is None for e in sink.events)

    async def test_finished_event_carries_the_aborting_error(self):
        sink = RecordingSink()
        phases = [FakePhase(PassPhaseName.CATALOG, SyncOutcome.PROVIDER_ABORT)]
        await CollectionPass(_run(sink), phases).run()
        finished = sink.events[-1]
        assert finished.event_type is T.PASS_FINISHED
        assert finished.error == "boom"
        assert finished.duration_ms is not None

    async def test_sink_failure_never_aborts_the_pass(self):
        sink = MagicMock()
        sink.emit.side_effect = OSError("disk full")
        run = _run()
        result = await CollectionPass(
            KalshiRun(**{**vars(run), "sink": sink}),
            [FakePhase(PassPhaseName.CATALOG)],
        ).run()
        assert result.outcome is SyncOutcome.OK


@pytest.mark.asyncio
class TestPassResult:
    async def test_to_dict_round_trips_through_json(self):
        result = await CollectionPass(_run(), [FakePhase(PassPhaseName.CATALOG)]).run()
        payload = json.loads(json.dumps(result.to_dict()))
        assert payload["run_id"] == str(result.run_id)
        assert payload["started_at"] == NOW.isoformat()
        assert payload["outcome"] == "ok"
        assert payload["phases"] == [
            {
                "name": "catalog",
                "outcome": "ok",
                "duration_ms": 5,
                "summary": {"seen": True},
            }
        ]
        assert "exit_code" not in payload  # the CLI adds it; no integers here

    async def test_skipped_phase_serializes_as_skipped(self):
        phases = [
            FakePhase(PassPhaseName.CATALOG, SyncOutcome.STORAGE_ABORT),
            FakePhase(PassPhaseName.CATALOG),
        ]
        result = await CollectionPass(_run(), phases).run()
        payload = result.to_dict()
        assert payload["phases"][1]["outcome"] == "skipped"
        assert isinstance(result, PassResult)


# ---------------------------------------------------------------------------
# The real catalog phase (Task 3.3)
# ---------------------------------------------------------------------------


def _catalog_run(h: Harness) -> KalshiRun:
    """A ``KalshiRun`` whose client and connection are the harness's fakes.

    ``KalshiRun.client`` is a full ``KalshiClient`` in production; the fake
    source stands in for it and carries the ``mode``/``rate_limit`` the
    pass's start line reports. ``CatalogPhase`` builds
    ``CatalogRepository(run.conn)``; the ``passthrough_repository`` fixture
    makes that constructor return the fake repository unchanged.
    """
    return KalshiRun(
        settings=MagicMock(),
        client=cast("KalshiClient", h.source),
        # the passthrough_repository fixture hands this straight back
        conn=cast("AsyncConnection[Any]", h.repo),
        sink=h.sink,
        run_id=uuid4(),
        clock=lambda: h.now,
    )


@pytest.fixture
def h() -> Harness:
    harness = Harness()
    harness.seed_parents()
    return harness


@pytest.fixture
def passthrough_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CatalogRepository(conn)`` returns the fake repository unchanged."""
    monkeypatch.setattr(
        "manta_trading.data.kalshi.repository.CatalogRepository",
        lambda conn: conn,
    )


class TestPassPhases:
    def test_catalog_then_candles_by_name_and_order(self):
        """Criterion 1 (slice 264): the catalog is current before candles run."""
        assert [p.name for p in PASS_PHASES] == [
            PassPhaseName.CATALOG,
            PassPhaseName.CANDLES,
        ]
        assert isinstance(PASS_PHASES[0], CatalogPhase)
        assert isinstance(PASS_PHASES[1], CandlesPhase)

    async def test_catalog_abort_leaves_the_candle_phase_skipped(self):
        catalog = FakePhase(PassPhaseName.CATALOG, SyncOutcome.PROVIDER_ABORT)
        candles = FakePhase(PassPhaseName.CANDLES)
        result = await CollectionPass(_run(), [catalog, candles]).run()
        assert candles.calls == 0
        assert [(r.name, r.outcome) for r in result.reports] == [
            (PassPhaseName.CATALOG, SyncOutcome.PROVIDER_ABORT),
            (PassPhaseName.CANDLES, SKIPPED),
        ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("passthrough_repository")
class TestCatalogPhase:
    async def test_ok_reports_the_sync_summary(self, h: Harness):
        h.live_market("M1")
        report = await CatalogPhase().run(_catalog_run(h))
        assert report.name is PassPhaseName.CATALOG
        assert report.outcome is SyncOutcome.OK
        assert report.error is None
        assert report.summary["phases"]["markets"]["written"] == 1
        assert report.summary["run_id"] and report.duration_ms >= 0

    async def test_item_error_reports_partial(self, h: Harness):
        h.live_market("OK1")
        h.live_market("BAD", status="not-a-status")
        report = await CatalogPhase().run(_catalog_run(h))
        assert report.outcome is SyncOutcome.PARTIAL
        assert report.error is None
        assert len(report.summary["item_errors"]) == 1

    async def test_provider_error_reports_provider_abort(self, h: Harness):
        h.live_market("M1")
        h.source.raise_on("get_series_list", ProviderTransientError("503"))
        report = await CatalogPhase().run(_catalog_run(h))
        assert report.outcome is SyncOutcome.PROVIDER_ABORT
        assert "503" in (report.error or "")

    async def test_operational_error_reports_storage_abort(self, h: Harness):
        h.live_market("M1")
        h.repo.fail_on("upsert_markets", errors.OperationalError("connection lost"))
        report = await CatalogPhase().run(_catalog_run(h))
        assert report.outcome is SyncOutcome.STORAGE_ABORT
        assert "connection lost" in (report.error or "")

    async def test_full_pass_event_order_and_one_run_id(self, h: Harness):
        """Criterion 4 against the fakes; the integration tier repeats it
        against the real repository."""
        h.live_market("M1")
        run = _catalog_run(h)
        result = await CollectionPass(run, [CatalogPhase()]).run()
        assert h.sink.types() == [
            T.PASS_STARTED,
            T.RUN_STARTED,
            *[T.PHASE_FINISHED] * 5,
            T.RUN_FINISHED,
            T.PASS_FINISHED,
        ]
        assert {e.run_id for e in h.sink.events} == {run.run_id}
        assert result.outcome is SyncOutcome.OK


# ---------------------------------------------------------------------------
# The real candle phase (slice 264, Task 5.5)
# ---------------------------------------------------------------------------

RULE = CollectionRule(True, frozenset(), frozenset({"Sports"}), None, None)


@dataclass
class FakeConn:
    """Stands in for ``run.conn``: each phase's repository constructor is
    patched to pull its fake from here (``two_repositories``)."""

    catalog: Any
    candles: FakeCandleRepository


@pytest.fixture
def two_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "manta_trading.data.kalshi.repository.CatalogRepository",
        lambda conn: conn.catalog,
    )
    monkeypatch.setattr(
        "manta_trading.data.kalshi.candle_repository.CandleRepository",
        lambda conn, rule: conn.candles,
    )


def _two_phase_run(h: Harness, candles: FakeCandleRepository) -> KalshiRun:
    settings = MagicMock()
    settings.collection_rule.return_value = RULE
    return KalshiRun(
        settings=settings,
        client=cast("KalshiClient", h.source),
        conn=cast("AsyncConnection[Any]", FakeConn(h.repo, candles)),
        sink=h.sink,
        run_id=uuid4(),
        clock=lambda: h.now,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("two_repositories")
class TestCandlesPhase:
    async def test_ok_reports_the_candle_summary(self, h: Harness):
        candles = FakeCandleRepository()
        report = await CandlesPhase().run(_two_phase_run(h, candles))
        assert report.name is PassPhaseName.CANDLES
        assert report.outcome is SyncOutcome.OK
        assert report.error is None
        assert report.summary["cutoff"] == h.source.cutoff.market_settled_ts.isoformat()
        assert report.summary["pending"]["live"] == 0
        assert candles.sync_state is not None

    async def test_candle_abort_leaves_the_catalog_phase_intact(self, h: Harness):
        """Criterion 1's third clause: a candle abort cannot touch the catalog
        phase's outcome or its ``sync_state`` row (263 Decision 2)."""
        h.live_market("M1")
        # Only the candle phase calls the batch endpoint, so failing it is an
        # unambiguous candle-phase abort (the catalog reads the cutoff too).
        candles = FakeCandleRepository()
        candles.add_market(FakeMarket("M1", h.now - timedelta(hours=1), h.now))
        h.source.raise_on("get_markets_candlesticks", ProviderTransientError("503"))
        run = _two_phase_run(h, candles)
        result = await CollectionPass(run, [CatalogPhase(), CandlesPhase()]).run()
        assert [(r.name, r.outcome) for r in result.reports] == [
            (PassPhaseName.CATALOG, SyncOutcome.OK),
            (PassPhaseName.CANDLES, SyncOutcome.PROVIDER_ABORT),
        ]
        assert result.outcome is SyncOutcome.PROVIDER_ABORT
        # The catalog's state row stands as its phase left it (263's
        # CatalogPhase runs on the wall clock, so only presence is asserted)
        # and the candle side wrote nothing at all.
        catalog_state = h.repo.sync_state[Surface.CATALOG]
        assert catalog_state.last_full_sync_at is not None
        assert candles.sync_state is None and candles.state == {}
        assert result.reports[0].summary["phases"]["markets"]["written"] == 1
        assert result.reports[0].error is None

    async def test_operational_error_reports_storage_abort(self, h: Harness):
        candles = FakeCandleRepository()
        candles.fail_on("pending_live", errors.OperationalError("connection lost"))
        report = await CandlesPhase().run(_two_phase_run(h, candles))
        assert report.outcome is SyncOutcome.STORAGE_ABORT
        assert "connection lost" in (report.error or "")

    async def test_two_phase_pass_reports_both_in_order(self, h: Harness):
        h.live_market("M1")
        run = _two_phase_run(h, FakeCandleRepository())
        result = await CollectionPass(run, PASS_PHASES).run()
        payload = json.loads(json.dumps(result.to_dict()))
        assert [p["name"] for p in payload["phases"]] == ["catalog", "candles"]
        assert payload["outcome"] == "ok"
        assert {e.run_id for e in h.sink.events} == {run.run_id}
