"""Response models for the futures tick-bar routes (slice 308 D2, D7).

Bars of N trades stay row records, like time bars: prices are exact text from
``price_text`` and times nine-digit ISO text from ``ts_text`` (231 TD5). Rows
arrive in ``TICK_BAR_COLUMNS`` order from ``read_tick_bars``. The plan's facts
come from ``plan_envelope``, shared with the other futures time series.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from manta_trading.api_server.models.futures import (
    FuturesTarget,
    IncompleteRecord,
    SegmentRecord,
    UnresolvedRecord,
    plan_envelope,
)
from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.tick_bar_reads import TICK_BAR_COLUMNS
from manta_trading.data.tick.tick_format import price_text, ts_text
from manta_trading.data.tick.tick_reads import Row


class FuturesTickBarRecord(BaseModel):
    """One bar of ``ticks_per_bar`` trades (D2). ``partial`` is true only for
    a session's last bar holding fewer trades; ``buy_volume + sell_volume`` is
    below ``volume`` by the trades with no aggressor side."""

    session_date: date
    index: int
    open_time: str
    close_time: str
    raw_symbol: str
    instrument_id: int
    open: str | None
    high: str | None
    low: str | None
    close: str | None
    volume: int
    trade_count: int
    buy_volume: int
    sell_volume: int
    bid_px: str | None
    ask_px: str | None
    partial: bool

    @classmethod
    def from_row(cls, row: Row) -> FuturesTickBarRecord:
        cells = dict(zip(TICK_BAR_COLUMNS, row, strict=True))
        for name in ("open_time", "close_time"):
            cells[name] = ts_text(cells[name])
        for name in ("open", "high", "low", "close", "bid_px", "ask_px"):
            raw = cells[name]
            cells[name] = None if raw is None else price_text(raw)
        return cls.model_validate(cells)


class FuturesTickBarsResponse(BaseModel):
    """``GET /api/v1/futures/{contracts/{symbol} | products/{product}}/tick-bars``.

    ``end`` is exclusive (session dates). ``from``/``until`` echo the instant
    bounds, half-open, null when not given; they select bars by ``open_time``.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    target: FuturesTarget
    ticks_per_bar: int
    start: date | None
    end: date | None
    # ``from`` is a Python keyword: the field is ``from_``, the wire name its alias.
    from_: datetime | None = Field(alias="from")
    until: datetime | None
    definitions_as_of: date | None
    segments: list[SegmentRecord]
    unresolved: list[UnresolvedRecord]
    incomplete: list[IncompleteRecord]
    count: int
    bars: list[FuturesTickBarRecord]

    @classmethod
    def from_plan(
        cls,
        plan: GetPlan,
        ticks_per_bar: int,
        rows: Sequence[Row],
        bounds: tuple[datetime | None, datetime | None],
    ) -> FuturesTickBarsResponse:
        # Through the alias, as ``FuturesTicksResponse`` does: ``from`` is a keyword.
        return cls(
            **plan_envelope(plan)
            | {
                "ticks_per_bar": ticks_per_bar,
                "from": bounds[0],
                "until": bounds[1],
                "count": len(rows),
                "bars": [FuturesTickBarRecord.from_row(row) for row in rows],
            }
        )
