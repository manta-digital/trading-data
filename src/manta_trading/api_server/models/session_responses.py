"""The session bars response for ``GET /api/v1/bars/{symbol}`` (slice 174 D9).

Only a session request (``session=regular|extended``) returns these fields; a
default request keeps ``BarsResponse`` unchanged apart from ``applied.session``,
so default bars do not pay for two extra keys each.
"""

from __future__ import annotations

from datetime import date
from typing import cast

import pandas as pd

from manta_trading.api_server.models.responses import (
    AppliedEquityBarParams,
    BarRecord,
    BarsResponse,
)
from manta_trading.constants import SessionPhase


class SessionBarRecord(BarRecord):
    """A session-aligned bar. ``timestamp`` is the bucket's start; the last bar
    of a phase can be shorter than the granularity."""

    session_date: date
    """The exchange session the bar belongs to (its date in the calendar's zone)."""
    phase: SessionPhase
    """``pre``, ``regular`` or ``post``; a bar never crosses a phase."""


class SessionBarsResponse(BarsResponse):
    """Response body for a session request to ``GET /api/v1/bars/{symbol}``."""

    bars: list[SessionBarRecord]  # type: ignore[assignment]

    @classmethod
    def from_dataframe(
        cls,
        symbol: str,
        df: pd.DataFrame,
        *,
        applied: AppliedEquityBarParams,
        is_stale: bool,
    ) -> SessionBarsResponse:
        """Build from ``get_session_bars``' frame (OHLCV, ``session_date``,
        ``phase``, indexed by bucket start)."""
        bars = [
            SessionBarRecord(
                timestamp=cast(pd.Timestamp, idx).to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                session_date=row["session_date"],
                phase=SessionPhase(row["phase"]),
            )
            for idx, row in df.iterrows()
        ]
        return cls(
            symbol=symbol,
            granularity=applied.granularity,
            adjusted=applied.adjusted,
            prints=applied.prints,
            is_stale=is_stale,
            applied=applied,
            count=len(bars),
            bars=bars,
        )
