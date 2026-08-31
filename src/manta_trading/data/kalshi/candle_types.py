"""Candle-phase value types shared by the planner, the core, and the pass.

``CandleResult`` is what one phase reports, ``CandleSource`` what it needs
from the client, ``classify_candles`` how its outcome is decided. The
collection rule the phase applies is ``selection.CollectionRule`` (moved
there in slice 265, Decision 3, so candles and trades share one rule).
Nothing here imports the client or the repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import psycopg

from manta_trading.data.kalshi.constants import CandlePeriod
from manta_trading.data.kalshi.models import HistoricalCutoff, MarketCandlesticks
from manta_trading.data.kalshi.sync_types import SyncOutcome, classify_outcome
from manta_trading.providers.errors import ProviderError

# ---------------------------------------------------------------------------
# The phase's result, its source, and its classification (Section 5)
# ---------------------------------------------------------------------------


class CandleSource(Protocol):
    """The two client calls the candle core makes (design *Client and
    models*). ``KalshiClient`` satisfies it structurally; tests substitute a
    fake that serves scripted candles and records every query."""

    async def get_markets_candlesticks(
        self,
        tickers: Sequence[str],
        *,
        start_ts: int,
        end_ts: int,
        period_interval: CandlePeriod,
    ) -> list[MarketCandlesticks]: ...

    async def get_historical_cutoff(self) -> HistoricalCutoff: ...


@dataclass(frozen=True)
class CandleItemError:
    """One per-market failure of the phase (Decision 7: only omission)."""

    ticker: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"ticker": self.ticker, "reason": self.reason}


@dataclass
class CandleResult:
    """What one candle phase did — JSON-serializable through :meth:`to_dict`
    (design *``CandleResult.to_dict()``*)."""

    run_id: UUID
    started_at: datetime
    period: CandlePeriod
    cutoff: datetime | None = None
    pending_live: int = 0
    pending_finishing: int = 0
    pending_backlog: int = 0
    backlog_remaining: int = 0
    behind_cutoff: int = 0
    requests: int = 0
    markets_requested: int = 0
    markets_advanced: int = 0
    candles_fetched: int = 0
    candles_written: int = 0
    item_errors: list[CandleItemError] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None

    def counts(self) -> dict[str, int]:
        """The integer counts the ``phase_finished`` event carries."""
        return {
            "pending_live": self.pending_live,
            "pending_finishing": self.pending_finishing,
            "pending_backlog": self.pending_backlog,
            "backlog_remaining": self.backlog_remaining,
            "behind_cutoff": self.behind_cutoff,
            "requests": self.requests,
            "markets_requested": self.markets_requested,
            "markets_advanced": self.markets_advanced,
            "candles_fetched": self.candles_fetched,
            "candles_written": self.candles_written,
            "item_errors": len(self.item_errors),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "started_at": self.started_at.isoformat(),
            "period": int(self.period),
            "cutoff": self.cutoff.isoformat() if self.cutoff else None,
            "pending": {
                "live": self.pending_live,
                "finishing": self.pending_finishing,
                "backlog": self.pending_backlog,
                "backlog_remaining": self.backlog_remaining,
            },
            "requests": self.requests,
            "markets_requested": self.markets_requested,
            "markets_advanced": self.markets_advanced,
            "candles_fetched": self.candles_fetched,
            "candles_written": self.candles_written,
            "item_errors": [e.to_dict() for e in self.item_errors],
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def classify_candles(
    result: CandleResult, exc: ProviderError | psycopg.OperationalError | None
) -> SyncOutcome:
    """Pure classification of a finished (or aborted) candle phase — the same
    rule as the catalog's (``sync_types.classify_outcome``)."""
    return classify_outcome(bool(result.item_errors), exc)
