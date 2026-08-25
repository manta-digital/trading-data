"""Shared setup for the sync-core unit tests: fakes, recording sink, seeds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from manta_trading.data.kalshi.constants import MarketStatus, MarketStatusFilter
from manta_trading.data.kalshi.events import SyncEvent, SyncEventType
from manta_trading.data.kalshi.models import Market
from manta_trading.data.kalshi.sync import CatalogSync

from ._fake_repository import FakeCatalogRepository
from ._fake_source import FakeCatalogSource, make_event, make_market, make_series

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
SERIES = "S1"
EVENT = "E1"


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[SyncEvent] = []

    def emit(self, event: SyncEvent) -> None:
        self.events.append(event)

    def types(self) -> list[SyncEventType]:
        return [e.event_type for e in self.events]

    def phases(self) -> list[str | None]:
        return [
            e.phase for e in self.events if e.event_type is SyncEventType.PHASE_FINISHED
        ]


class Harness:
    """A fake source, fake repository, recording sink and a core wired to them."""

    def __init__(
        self,
        *,
        now: datetime = NOW,
        page_size: int | None = None,
        load_fixtures: bool = False,
    ) -> None:
        self.now = now
        self.source = FakeCatalogSource(
            page_size=page_size, load_fixtures=load_fixtures
        )
        self.repo = FakeCatalogRepository(now=now)
        self.sink = RecordingSink()
        self.core = self.new_core()

    def new_core(self) -> CatalogSync:
        """A fresh run over the same source/repository (a second pass)."""
        self.sink = RecordingSink()
        self.core = CatalogSync(
            self.source, self.repo, self.sink, clock=lambda: self.now
        )
        return self.core

    def seed_parents(self) -> None:
        self.source.add_series(make_series(SERIES))
        self.source.add_events(make_event(EVENT, SERIES))

    def live_market(
        self,
        ticker: str,
        status_filter: MarketStatusFilter = MarketStatusFilter.OPEN,
        *,
        status: str = MarketStatus.ACTIVE.value,
        close_time: datetime | None = None,
        event: str = EVENT,
    ) -> Market:
        market = make_market(
            ticker,
            event,
            status=status,
            close_time=close_time or self.now + timedelta(days=1),
            result=None,
            settlement_ts=None,
        )
        self.source.add_live(status_filter, market)
        return market

    def settled_market(
        self, ticker: str, settlement_ts: datetime, *, event: str = EVENT
    ) -> Market:
        market = make_market(
            ticker,
            event,
            status=MarketStatus.FINALIZED.value,
            result="yes",
            close_time=settlement_ts - timedelta(minutes=1),
            settlement_ts=settlement_ts,
        )
        self.source.add_settled(market)
        return market
