"""Pydantic response models for the Data Serving API."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from manta_trading.cli.rendering.status_table import StatusRow
    from manta_trading.constants import Granularity
    from manta_trading.data.maintenance.status_coverage import CoverageFreshness
    from manta_trading.market.maintenance.cagg_freshness import FreshnessVerdict


class HealthResponse(BaseModel):
    """Response body for ``GET /api/v1/health``.

    HTTP status is always 200 — callers distinguish a healthy DB from a
    failing one via the ``db`` field, not the HTTP status code.
    """

    model_config = ConfigDict()

    status: Literal["ok"]
    db: Literal["ok", "error"]
    coverage: Literal["ok", "stale"] | None = None
    """Freshness of the two ``data_status`` coverage caggs (slice 185 D6).

    Populated only when ``db == "ok"`` — on a DB outage "stale" would be
    meaningless noise on top of a real failure. ``exclude_none=True``
    serialization keeps the key absent in that case, so no existing client
    contract changes.
    """

    detail: str | None = None


class BarRecord(BaseModel):
    """A single OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarsResponse(BaseModel):
    """Response body for ``GET /api/v1/bars/{symbol}``."""

    symbol: str
    granularity: str
    adjusted: bool
    count: int
    bars: list[BarRecord]

    @classmethod
    def from_dataframe(
        cls,
        symbol: str,
        granularity: Granularity,
        adjusted: bool,
        df: pd.DataFrame,
    ) -> BarsResponse:
        """Build a ``BarsResponse`` from a DB result DataFrame.

        The DataFrame index must be a ``DatetimeIndex``; columns must
        include ``open``, ``high``, ``low``, ``close``, and ``volume``.
        """
        bars = [
            BarRecord(
                timestamp=cast(pd.Timestamp, idx).to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
            )
            for idx, row in df.iterrows()
        ]
        return cls(
            symbol=symbol,
            granularity=str(granularity),
            adjusted=adjusted,
            count=len(bars),
            bars=bars,
        )


class AvailableRange(BaseModel):
    """Date range over which data is available for a granularity group."""

    start: date
    end: date


class SymbolSummary(BaseModel):
    """Condensed instrument metadata for list responses."""

    symbol: str
    exchange: str | None
    type: str | None
    asset_class: str | None
    active: bool


class SymbolsResponse(BaseModel):
    """Response body for ``GET /api/v1/symbols``."""

    symbols: list[SymbolSummary]
    count: int


class SymbolDetail(BaseModel):
    """Full instrument metadata plus available data ranges."""

    symbol: str
    exchange: str | None
    type: str | None
    asset_class: str | None
    active: bool
    available: dict[str, AvailableRange]


class GapRecord(BaseModel):
    """A single row from the ``data_gaps`` table."""

    gap_start: datetime
    gap_end: datetime
    granularity: str  # DB family: 'daily' or 'minute'
    fetch_status: str
    attempt_count: int
    last_attempt_ts: datetime | None


class GapsResponse(BaseModel):
    """Response body for ``GET /api/v1/gaps/{symbol}``."""

    symbol: str
    count: int
    gaps: list[GapRecord]


class CoverageVerdict(BaseModel):
    """One coverage cagg's freshness verdict, serialized (slice 185 D2).

    The wire form of ``cagg_freshness.FreshnessVerdict``: ``timedelta`` fields
    become float seconds and ``StalenessSignal`` members become their string
    values. Nothing is recomputed here — this is a projection.
    """

    view_name: str
    is_fresh: bool
    signals: list[str]
    lag_seconds: float | None
    threshold_seconds: float | None
    detail: str

    @classmethod
    def from_verdict(cls, verdict: FreshnessVerdict) -> CoverageVerdict:
        """Project a domain verdict onto the response model.

        ``lag``/``threshold`` are ``timedelta | None``; ``None`` is preserved
        rather than collapsed to ``0.0`` — "could not be measured" and "no lag"
        are different facts, and a client must be able to tell them apart.
        """
        return cls(
            view_name=verdict.view_name,
            is_fresh=verdict.is_fresh,
            signals=[signal.value for signal in verdict.signals],
            lag_seconds=None if verdict.lag is None else verdict.lag.total_seconds(),
            threshold_seconds=(
                None if verdict.threshold is None else verdict.threshold.total_seconds()
            ),
            detail=verdict.detail,
        )


class CoverageStatus(BaseModel):
    """Freshness of the caggs behind ``data_status``, one per ``COVERAGE_VIEWS``."""

    is_stale: bool
    verdicts: list[CoverageVerdict]

    @classmethod
    def from_freshness(cls, freshness: CoverageFreshness) -> CoverageStatus:
        """Project ``CoverageFreshness``, preserving ``COVERAGE_VIEWS`` order."""
        return cls(
            is_stale=freshness.is_stale,
            verdicts=[
                CoverageVerdict.from_verdict(verdict) for verdict in freshness.verdicts
            ],
        )


class StatusRowRecord(BaseModel):
    """One ``data_status`` row — the wire form of ``StatusRow``."""

    symbol: str
    granularity: str
    health: str
    bars_stored: int | None
    first_bar_ts: datetime | None
    last_bar_ts: datetime | None
    gap_count: int | None
    last_attempt_ts: datetime | None
    last_attempt_outcome: str | None
    target_end_ts: datetime | None
    effective_start: date | None

    @classmethod
    def from_status_row(cls, row: StatusRow) -> StatusRowRecord:
        """Project a ``StatusRow`` field-for-field by name."""
        return cls(
            symbol=row.symbol,
            granularity=row.granularity,
            health=row.health,
            bars_stored=row.bars_stored,
            first_bar_ts=row.first_bar_ts,
            last_bar_ts=row.last_bar_ts,
            gap_count=row.gap_count,
            last_attempt_ts=row.last_attempt_ts,
            last_attempt_outcome=row.last_attempt_outcome,
            target_end_ts=row.target_end_ts,
            effective_start=row.effective_start,
        )


class StatusResponse(BaseModel):
    """Response body for ``GET /api/v1/status`` (slice 185 D2).

    ``summary`` is always the full-universe health-count breakdown, unfiltered
    by ``symbol``/``health`` — it answers "how healthy is the whole registry",
    independent of what ``rows`` happens to show.
    """

    scope: Literal["symbol", "all"]
    symbol: str | None
    count: int
    rows: list[StatusRowRecord]
    summary: dict[str, int]
    coverage: CoverageStatus
