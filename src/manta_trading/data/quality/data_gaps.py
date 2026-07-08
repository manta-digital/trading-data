"""DataGap read DTO — mirrors the data_gaps table columns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from manta_trading.data.acquisition.state import Granularity
from manta_trading.data.quality.fetch_status import FetchStatus


@dataclass(frozen=True)
class DataGap:
    """Read-only representation of one row from the data_gaps table.

    Writers are introduced in slice 144. This DTO is read-only.
    """

    symbol: str
    granularity: Granularity
    gap_start: datetime
    gap_end: datetime
    fetch_status: FetchStatus
    last_attempt_ts: datetime | None
    attempt_count: int
