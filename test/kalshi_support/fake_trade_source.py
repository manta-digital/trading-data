"""``FakeTradeSource`` — an in-memory ``TradeSource`` for trades-core tests.

Serves a scripted tape through ``GET /markets/trades`` with the live
endpoint's window semantics as the core relies on them (Decision 1):
``min_ts`` is a strict "after" and ``max_ts`` an inclusive "through", both at
second granularity; newest first; ``limit`` rows per page with an opaque
cursor that is empty on the last page. Records every query — the recorded
``min_ts`` / ``max_ts`` are what proves, at the unit level, which windows
the core asked for. ``page_size`` overrides the requested ``limit`` so a
test can script several pages per window with a handful of rows.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from kalshi_support.fake_source import load_fixture
from kalshi_support.samples import TRADE_SAMPLE
from manta_trading.data.kalshi.models import HistoricalCutoff, Trade, TradesPage
from manta_trading.data.kalshi.sync_types import epoch


def make_trade(ticker: str, created: datetime, **overrides: object) -> Trade:
    """A live-shaped trade (``TRADE_SAMPLE`` with a fresh id and overrides)."""
    return Trade.model_validate(
        {
            **TRADE_SAMPLE,
            "ticker": ticker,
            "trade_id": str(uuid4()),
            "created_time": created.isoformat(),
            **overrides,
        }
    )


class FakeTradeSource:
    """See the module docstring."""

    def __init__(self, *, page_size: int | None = None) -> None:
        self.tape: list[Trade] = []
        self.page_size = page_size
        self.cutoff = HistoricalCutoff.model_validate(load_fixture("historical_cutoff"))
        #: Every trades query: ``{"cursor", "min_ts", "max_ts", "limit"}``.
        self.trade_queries: list[dict[str, object]] = []
        self.calls: list[str] = []
        self._failures: list[
            tuple[
                str,
                BaseException,
                int | None,
                Callable[[dict[str, object]], bool] | None,
            ]
        ] = []
        self._counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Test-side setup
    # ------------------------------------------------------------------

    def add_trades(self, *rows: Trade) -> None:
        self.tape.extend(rows)

    def set_cutoff(self, trades_created_ts: datetime) -> None:
        self.cutoff = self.cutoff.model_copy(
            update={"trades_created_ts": trades_created_ts}
        )

    def raise_on(
        self,
        call: str,
        exc: BaseException,
        *,
        at: int | None = None,
        when: Callable[[dict[str, object]], bool] | None = None,
    ) -> None:
        """Raise ``exc`` on the ``at``-th invocation of ``call`` and/or when
        ``when(query)`` is true (``call`` is the method name)."""
        self._failures.append((call, exc, at, when))

    def _record(self, call: str, query: dict[str, object]) -> None:
        self.calls.append(call)
        self._counts[call] = self._counts.get(call, 0) + 1
        for name, exc, at, when in self._failures:
            if name != call:
                continue
            if at is not None and self._counts[call] != at:
                continue
            if when is not None and not when(query):
                continue
            raise exc

    # ------------------------------------------------------------------
    # TradeSource
    # ------------------------------------------------------------------

    async def get_trades(
        self, *, cursor: str | None = None, min_ts: int, max_ts: int, limit: int
    ) -> TradesPage:
        query: dict[str, object] = {
            "cursor": cursor,
            "min_ts": min_ts,
            "max_ts": max_ts,
            "limit": limit,
        }
        self.trade_queries.append(query)
        self._record("get_trades", query)
        rows = sorted(
            (t for t in self.tape if min_ts < epoch(t.created_time) <= max_ts),
            key=lambda t: t.created_time,
            reverse=True,
        )
        size = self.page_size or limit
        offset = int(cursor) if cursor else 0
        page = rows[offset : offset + size]
        next_offset = offset + size
        return TradesPage(
            trades=page, cursor=str(next_offset) if next_offset < len(rows) else ""
        )

    async def get_historical_cutoff(self) -> HistoricalCutoff:
        self._record("get_historical_cutoff", {})
        return self.cutoff
