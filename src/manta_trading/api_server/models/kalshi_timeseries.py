"""Pydantic response models for the Kalshi time-series routes (slice 188).

Split from ``kalshi_catalog`` to keep each module near the ~300-line guide.

The candle wire shape is Kalshi's own: the fourteen flat stored columns are
re-nested into ``yes_bid`` / ``yes_ask`` / ``price`` on the way out, driven by
``candle_repository.CANDLE_COLUMNS`` — the same mapping the collection phase
flattens *with*. Neither direction restates the other, so a column added there
is served here without an edit.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic needs the runtime type
from decimal import Decimal  # noqa: TC003 — pydantic needs the runtime type
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from manta_trading.data.kalshi.candle_repository import CANDLE_COLUMNS

if TYPE_CHECKING:
    from manta_trading.data.kalshi.serve_timeseries import (
        CandleRow,
        MarketContext,
        TapeFacts,
        TradeRow,
    )


class CandleBidAsk(BaseModel):
    """One side's OHLC over the period, in dollars."""

    open_dollars: Decimal | None
    high_dollars: Decimal | None
    low_dollars: Decimal | None
    close_dollars: Decimal | None


class CandlePrice(BaseModel):
    """Traded price over the period: OHLC plus the previous and mean marks.

    A period with no trades carries ``previous_dollars`` alone; the other
    fields stay null rather than the object being dropped, so every candle has
    the same shape.
    """

    open_dollars: Decimal | None
    high_dollars: Decimal | None
    low_dollars: Decimal | None
    close_dollars: Decimal | None
    previous_dollars: Decimal | None
    mean_dollars: Decimal | None


#: Which model each nested candle object uses. The field *names* come from
#: ``CANDLE_COLUMNS``; this only says which object holds them.
_CANDLE_OBJECTS: dict[str, type[BaseModel]] = {
    "yes_bid": CandleBidAsk,
    "yes_ask": CandleBidAsk,
    "price": CandlePrice,
}


class CandleRecord(BaseModel):
    """One candlestick, in the shape Kalshi serves and ``Candlestick`` parses."""

    end_period_ts: datetime
    yes_bid: CandleBidAsk
    yes_ask: CandleBidAsk
    price: CandlePrice
    volume_fp: Decimal | None
    open_interest_fp: Decimal | None

    @classmethod
    def from_row(cls, row: CandleRow) -> CandleRecord:
        """Re-nest the stored columns by walking ``CANDLE_COLUMNS``.

        Its second element is already the ``(object, field)`` path the wire
        shape wants, so the mapping is followed rather than restated.
        """
        nested: dict[str, dict[str, Decimal | None]] = {
            name: {} for name in _CANDLE_OBJECTS
        }
        flat: dict[str, Decimal | None] = {}
        for value, (_, path) in zip(row.values, CANDLE_COLUMNS, strict=True):
            if len(path) == 1:
                flat[path[0]] = value
            else:
                obj, field = path
                nested[obj][field] = value
        return cls(
            end_period_ts=row.end_period_ts,
            yes_bid=CandleBidAsk(**nested["yes_bid"]),
            yes_ask=CandleBidAsk(**nested["yes_ask"]),
            price=CandlePrice(**nested["price"]),
            volume_fp=flat["volume_fp"],
            open_interest_fp=flat["open_interest_fp"],
        )


class CandlesResponse(BaseModel):
    """Candlesticks for one market over the requested window."""

    market_ticker: str
    period_minutes: int = Field(
        description=(
            "Candle period in minutes. One value is collected today; it is "
            "reported so a client need not assume it."
        )
    )
    collected: bool = Field(
        description=(
            "Whether this market is in the candle collection set. False means "
            "no candles are stored for it — not that the window was empty."
        )
    )
    coverage_from: datetime | None = Field(
        description=(
            "Oldest period this market's candles have been collected from. "
            "Null when the market is not collected."
        )
    )
    complete_through: datetime | None = Field(
        description=(
            "Requested and stored through this instant — not 'the newest "
            "stored candle'. A quiet market produces no candle for a period "
            "it was nonetheless asked for, so the two differ."
        )
    )
    count: int
    candlesticks: list[CandleRecord]

    @classmethod
    def build(
        cls, context: MarketContext, period_minutes: int, rows: list[CandleRow]
    ) -> CandlesResponse:
        records = [CandleRecord.from_row(row) for row in rows]
        return cls(
            market_ticker=context.ticker,
            period_minutes=period_minutes,
            collected=context.candle_collected,
            coverage_from=context.candle_coverage_from,
            complete_through=context.candle_complete_through,
            count=len(records),
            candlesticks=records,
        )


class TradeRecord(BaseModel):
    """One trade off the tape. Field names are the Kalshi/DB names verbatim."""

    created_time: datetime
    trade_id: str
    count_fp: Decimal
    yes_price_dollars: Decimal
    no_price_dollars: Decimal
    taker_outcome_side: str | None
    taker_book_side: str | None
    is_block_trade: bool

    @classmethod
    def from_row(cls, row: TradeRow) -> TradeRecord:
        values = list(row.values)
        # ``trade_id`` is a UUID in storage; the wire carries its text form.
        return cls(
            created_time=values[0],
            trade_id=str(values[1]),
            count_fp=values[2],
            yes_price_dollars=values[3],
            no_price_dollars=values[4],
            taker_outcome_side=values[5],
            taker_book_side=values[6],
            is_block_trade=values[7],
        )


class TradesResponse(BaseModel):
    """Trades for one market over the requested window."""

    market_ticker: str
    coverage_from: datetime | None = Field(
        description=(
            "Oldest instant the trades tape reaches, across the live and "
            "historical surfaces. Null when the trades phase has never run."
        )
    )
    tape_complete_through: datetime | None = Field(
        description=(
            "Created time of the newest stored trade. Null means the trades "
            "phase has never run — not that the tape is empty today."
        )
    )
    tape_filtered: bool = Field(
        description=(
            "Whether this market's category is excluded from trade "
            "collection. True means no trades are stored for it by policy, so "
            "an empty window is expected rather than a gap."
        )
    )
    count: int
    trades: list[TradeRecord]

    @classmethod
    def build(
        cls,
        ticker: str,
        facts: TapeFacts | None,
        *,
        filtered: bool,
        rows: list[TradeRow],
    ) -> TradesResponse:
        records = [TradeRecord.from_row(row) for row in rows]
        return cls(
            market_ticker=ticker,
            coverage_from=None if facts is None else facts.coverage_from,
            tape_complete_through=(
                None if facts is None else facts.tape_complete_through
            ),
            tape_filtered=filtered,
            count=len(records),
            trades=records,
        )
