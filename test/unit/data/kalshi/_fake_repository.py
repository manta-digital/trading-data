"""``FakeCatalogRepository`` — in-memory twin of ``CatalogRepository``.

Same public surface over dicts: write-on-change by comparing ``raw``,
transitions from the prior status, an ``awaiting`` dict and a ``sync_state``
dict. Foreign-key and status-CHECK violations raise the same
``psycopg.errors`` classes the database would, so the sync core's integrity
fallback is exercised for real; ``fail_on`` injects any other exception.

``transaction()`` snapshots the state on entry and restores it when the
block raises, recording ``begin`` / ``commit`` / ``rollback`` in ``tx_log``
so per-page granularity is testable.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from psycopg import errors

from manta_trading.data.kalshi.constants import MarketStatus, Surface
from manta_trading.data.kalshi.models import Event, Market, Series
from manta_trading.data.kalshi.repository import MarketUpsertOutcome, SyncState

_VALID_STATUSES = {s.value for s in MarketStatus}


def _last_wins[T](rows: Sequence[T], key: str) -> list[T]:
    """Same page-level dedupe as ``CatalogRepository`` (one row per key)."""
    by_key: dict[str, T] = {getattr(r, key): r for r in rows}
    return list(by_key.values())


@dataclass
class AwaitingRow:
    close_time: datetime
    entered_at: datetime
    last_checked_at: datetime | None = None


@dataclass
class _State:
    series: dict[str, dict[str, Any]]
    events: dict[str, dict[str, Any]]
    markets: dict[str, Market]
    market_raw: dict[str, dict[str, Any]]
    awaiting: dict[str, AwaitingRow]
    sync_state: dict[Surface, SyncState]


class FakeCatalogRepository:
    """See the module docstring."""

    def __init__(self, *, now: datetime) -> None:
        self._now = now
        self._s = _State({}, {}, {}, {}, {}, {})
        self.writes: list[tuple[str, int]] = []
        self.tx_log: list[str] = []
        self._failures: list[tuple[str, BaseException, int]] = []
        self._counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def series(self) -> dict[str, dict[str, Any]]:
        return self._s.series

    @property
    def events(self) -> dict[str, dict[str, Any]]:
        return self._s.events

    @property
    def markets(self) -> dict[str, Market]:
        return self._s.markets

    @property
    def awaiting(self) -> dict[str, AwaitingRow]:
        return self._s.awaiting

    @property
    def sync_state(self) -> dict[Surface, SyncState]:
        return self._s.sync_state

    def fail_on(self, method: str, exc: BaseException, *, at: int = 1) -> None:
        """Raise ``exc`` on the ``at``-th call of ``method``."""
        self._failures.append((method, exc, at))

    def _enter(self, method: str) -> None:
        self._counts[method] = self._counts.get(method, 0) + 1
        for name, exc, at in self._failures:
            if name == method and self._counts[method] == at:
                raise exc

    def _wrote(self, method: str, count: int) -> None:
        self.writes.append((method, count))

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        snapshot = copy.deepcopy(self._s)
        self.tx_log.append("begin")
        try:
            yield
        except BaseException:
            self._s = snapshot
            self.tx_log.append("rollback")
            raise
        self.tx_log.append("commit")

    # ------------------------------------------------------------------
    # Upserts
    # ------------------------------------------------------------------

    async def upsert_series(self, rows: Sequence[Series]) -> int:
        self._enter("upsert_series")
        written = 0
        for row in _last_wins(rows, "ticker"):
            raw = row.model_dump(mode="json")
            if self._s.series.get(row.ticker) != raw:
                self._s.series[row.ticker] = raw
                written += 1
        self._wrote("upsert_series", written)
        return written

    async def upsert_events(self, rows: Sequence[Event]) -> int:
        self._enter("upsert_events")
        written = 0
        for row in _last_wins(rows, "event_ticker"):
            if row.series_ticker not in self._s.series:
                raise errors.ForeignKeyViolation(
                    f"events.series_ticker {row.series_ticker}"
                )
            raw = row.model_dump(mode="json", exclude={"markets"})
            if self._s.events.get(row.event_ticker) != raw:
                self._s.events[row.event_ticker] = raw
                written += 1
        self._wrote("upsert_events", written)
        return written

    async def upsert_markets(self, rows: Sequence[Market]) -> MarketUpsertOutcome:
        self._enter("upsert_markets")
        written = 0
        transitions: dict[tuple[str, str], int] = {}
        for row in _last_wins(rows, "ticker"):
            if row.status not in _VALID_STATUSES:
                raise errors.CheckViolation(f"markets_status_check {row.status}")
            if row.event_ticker not in self._s.events:
                raise errors.ForeignKeyViolation(
                    f"markets.event_ticker {row.event_ticker}"
                )
            raw = row.model_dump(mode="json")
            prior = self._s.markets.get(row.ticker)
            if self._s.market_raw.get(row.ticker) == raw:
                continue
            if prior is not None and prior.status != row.status:
                edge = (prior.status, row.status)
                transitions[edge] = transitions.get(edge, 0) + 1
            self._s.markets[row.ticker] = row
            self._s.market_raw[row.ticker] = raw
            written += 1
        self._wrote("upsert_markets", written)
        return MarketUpsertOutcome(written=written, transitions=transitions)

    # ------------------------------------------------------------------
    # Parent lookups
    # ------------------------------------------------------------------

    async def known_event_tickers(self, tickers: Iterable[str]) -> set[str]:
        self._enter("known_event_tickers")
        return {t for t in tickers if t in self._s.events}

    async def known_series_tickers(self, tickers: Iterable[str]) -> set[str]:
        self._enter("known_series_tickers")
        return {t for t in tickers if t in self._s.series}

    # ------------------------------------------------------------------
    # Awaiting set
    # ------------------------------------------------------------------

    async def enter_awaiting(self, now: datetime) -> int:
        self._enter("enter_awaiting")
        entered = 0
        for market in self._s.markets.values():
            if (
                market.close_time <= now
                and market.status != MarketStatus.FINALIZED.value
                and market.ticker not in self._s.awaiting
            ):
                self._s.awaiting[market.ticker] = AwaitingRow(market.close_time, now)
                entered += 1
        self._wrote("enter_awaiting", entered)
        return entered

    async def retire_awaiting(self) -> int:
        self._enter("retire_awaiting")
        gone = [
            t
            for t in self._s.awaiting
            if (m := self._s.markets.get(t)) is not None
            and m.status == MarketStatus.FINALIZED.value
            and m.result is not None
        ]
        for t in gone:
            del self._s.awaiting[t]
        self._wrote("retire_awaiting", len(gone))
        return len(gone)

    async def refresh_awaiting_close_times(self) -> int:
        self._enter("refresh_awaiting_close_times")
        changed = 0
        for ticker, row in self._s.awaiting.items():
            market = self._s.markets[ticker]
            if market.close_time != row.close_time:
                row.close_time = market.close_time
                changed += 1
        self._wrote("refresh_awaiting_close_times", changed)
        return changed

    async def awaiting_tickers(self) -> list[str]:
        self._enter("awaiting_tickers")
        return sorted(self._s.awaiting)

    async def mark_checked(self, tickers: Sequence[str], now: datetime) -> int:
        self._enter("mark_checked")
        count = 0
        for t in tickers:
            if t in self._s.awaiting:
                self._s.awaiting[t].last_checked_at = now
                count += 1
        self._wrote("mark_checked", count)
        return count

    # ------------------------------------------------------------------
    # sync_state
    # ------------------------------------------------------------------

    async def get_sync_state(self, surface: Surface) -> SyncState | None:
        self._enter("get_sync_state")
        return self._s.sync_state.get(surface)

    async def set_last_full_sync(self, surface: Surface, ts: datetime) -> None:
        self._enter("set_last_full_sync")
        current = self._s.sync_state.get(surface) or SyncState(None, None, None)
        self._s.sync_state[surface] = replace(current, last_full_sync_at=ts)
        self._wrote("set_last_full_sync", 1)

    async def set_watermark(self, surface: Surface, ts: datetime) -> None:
        self._enter("set_watermark")
        current = self._s.sync_state.get(surface) or SyncState(None, None, None)
        self._s.sync_state[surface] = replace(current, watermark_ts=ts)
        self._wrote("set_watermark", 1)
