"""Trades-phase value types shared by the core, the pass, and the renderer.

``TradeResult`` is what one phase reports, ``TradeSource`` what it needs
from the client, ``classify_trades`` how its outcome is decided,
``TradesBehindCutoffError`` the one condition that aborts the phase by
design (Decision 6). Nothing here imports the client or the repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

import psycopg

from manta_trading.data.kalshi.models import HistoricalCutoff, TradesPage
from manta_trading.data.kalshi.sync_types import SyncOutcome, classify_outcome, iso_utc
from manta_trading.providers.errors import ProviderError


class TradeSource(Protocol):
    """The two client calls the trades core makes (design *Core*).
    ``KalshiClient`` satisfies it structurally; tests substitute a fake that
    serves a scripted tape and records every query."""

    async def get_trades(
        self, *, cursor: str | None = None, min_ts: int, max_ts: int, limit: int
    ) -> TradesPage: ...

    async def get_historical_cutoff(self) -> HistoricalCutoff: ...


class WindowDirection(StrEnum):
    """Which way ``TradeSync.drain`` walks its one-hour windows (slice 267,
    Decision 5): the live phase forward from the watermark to the pass bound,
    the historical phase backward from the live floor to
    ``HISTORICAL_TRADES_FLOOR``. The watermark moves to each window's far
    edge in the walk's direction."""

    FORWARD = "forward"
    BACKWARD = "backward"


class TradesBehindCutoffError(Exception):
    """The tape watermark is behind the historical cutoff (Decision 6): the
    range between them is no longer served live, and nothing jumps forward.
    Propagates out of the pass; the historical phase (slice 267), which
    walks ``/historical/trades``, is the remedy.
    """

    def __init__(self, watermark: datetime, cutoff: datetime) -> None:
        self.watermark = watermark
        self.cutoff = cutoff
        super().__init__(
            f"trades watermark {watermark.isoformat()} is behind the historical "
            f"cutoff {cutoff.isoformat()}: the tape from {watermark.isoformat()} to "
            f"{cutoff.isoformat()} is no longer served live. Nothing was skipped; "
            "the historical phase (slice 267) drains that range from "
            "/historical/trades."
        )


@dataclass
class TradeResult:
    """What one trades phase did — JSON-serializable through :meth:`to_dict`
    (design *``TradeResult.to_dict()``*)."""

    run_id: UUID
    started_at: datetime
    cutoff: datetime | None = None
    coverage_from: datetime | None = None
    watermark_before: datetime | None = None
    watermark_after: datetime | None = None
    windows_completed: int = 0
    requests: int = 0
    trades_fetched: int = 0
    trades_written: int = 0
    unknown_market: int = 0
    excluded_by_rule: int = 0
    duplicates: int = 0
    capped: bool = False
    #: Data Flow step 2: no catalog walk has completed, so there is no pass
    #: bound and nothing was fetched — said explicitly, not inferred from zeros.
    catalog_missing: bool = False
    #: Display only (Decision 5): trades per unknown ticker prefix, for the
    #: once-per-phase log line. Nothing branches on it.
    unknown_prefixes: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None

    def counts(self) -> dict[str, int]:
        """The integer counts the ``phase_finished`` event carries."""
        return {
            "windows_completed": self.windows_completed,
            "requests": self.requests,
            "trades_fetched": self.trades_fetched,
            "trades_written": self.trades_written,
            "unknown_market": self.unknown_market,
            "excluded_by_rule": self.excluded_by_rule,
            "duplicates": self.duplicates,
            "capped": int(self.capped),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "started_at": self.started_at.isoformat(),
            "cutoff": iso_utc(self.cutoff),
            "coverage_from": iso_utc(self.coverage_from),
            "watermark": {
                "before": iso_utc(self.watermark_before),
                "after": iso_utc(self.watermark_after),
            },
            "windows_completed": self.windows_completed,
            "requests": self.requests,
            "capped": self.capped,
            "catalog_missing": self.catalog_missing,
            "trades_fetched": self.trades_fetched,
            "trades_written": self.trades_written,
            "unknown_market": self.unknown_market,
            "excluded_by_rule": self.excluded_by_rule,
            "duplicates": self.duplicates,
            "unknown_prefixes": dict(self.unknown_prefixes),
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def classify_trades(
    result: TradeResult, exc: ProviderError | psycopg.OperationalError | None
) -> SyncOutcome:
    """Pure classification of a finished (or aborted) trades phase — the
    catalog's rule (``sync_types.classify_outcome``) with no item errors:
    a page parses or its request fails, and unknown and excluded rows are
    counts, so this phase reports ``OK`` or an abort and **never**
    ``PARTIAL`` (Decision 9). ``result`` is accepted for the phase contract's
    symmetry with ``classify_candles``; nothing in it changes the outcome.
    """
    del result
    return classify_outcome(False, exc)
