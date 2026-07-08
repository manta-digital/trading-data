"""Pydantic response models for the Data Serving API."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from manta_trading.constants import Granularity


class HealthResponse(BaseModel):
    """Response body for ``GET /api/v1/health``.

    HTTP status is always 200 — callers distinguish a healthy DB from a
    failing one via the ``db`` field, not the HTTP status code.
    """

    model_config = ConfigDict()

    status: Literal["ok"]
    db: Literal["ok", "error"]
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
