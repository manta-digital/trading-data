"""Pydantic models for Kalshi ``trade-api/v2`` responses (external boundary).

Lenient by policy (design 261, Technical Decision 5): only the fields the
initiative consumes are required, ``extra="allow"`` keeps unknown upstream
fields (capture before it disappears), and fixed-point money/quantity strings
(``*_dollars``, ``*_fp``) parse to :class:`~decimal.Decimal` exactly.

Field sets follow what the live API actually serves (samples fetched
2026-08-24; recorded fixtures under ``test/fixtures/kalshi/`` are the
regression input), not prose documentation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class KalshiModel(BaseModel):
    """Base for every response object: tolerate unknown fields."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Catalog objects
# ---------------------------------------------------------------------------


class SettlementSource(KalshiModel):
    name: str | None = None
    url: str | None = None


class Series(KalshiModel):
    """One row of ``GET /series`` / ``GET /series/{series_ticker}``."""

    ticker: str
    frequency: str | None = None
    title: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    settlement_sources: list[SettlementSource] | None = None
    fee_type: str | None = None
    fee_multiplier: Decimal | None = None
    product_metadata: dict[str, Any] | None = None
    last_updated_ts: datetime | None = None


class Event(KalshiModel):
    """One row of ``GET /events`` / ``GET /events/{event_ticker}``.

    ``markets`` is populated only when ``with_nested_markets=true``.
    """

    event_ticker: str
    series_ticker: str
    title: str | None = None
    sub_title: str | None = None
    category: str | None = None
    mutually_exclusive: bool | None = None
    strike_date: datetime | None = None
    strike_period: str | None = None
    collateral_return_type: str | None = None
    product_metadata: dict[str, Any] | None = None
    last_updated_ts: datetime | None = None
    markets: list[Market] | None = None


class Market(KalshiModel):
    """One row of ``GET /markets`` / ``GET /markets/{ticker}``.

    ``status`` is kept as ``str`` deliberately: the CHECK constraint on
    ``kalshi.markets.status`` (derived from ``MarketStatus``) is where an
    undocumented value fails loudly, not the parse — a new status must not
    poison a whole page of otherwise-valid markets.
    """

    ticker: str
    event_ticker: str
    status: str
    close_time: datetime
    market_type: str | None = None
    title: str | None = None
    subtitle: str | None = None
    yes_sub_title: str | None = None
    no_sub_title: str | None = None
    rules_primary: str | None = None
    rules_secondary: str | None = None
    # Lifecycle timestamps (Kalshi's).
    created_time: datetime | None = None
    open_time: datetime | None = None
    expiration_time: datetime | None = None
    expected_expiration_time: datetime | None = None
    latest_expiration_time: datetime | None = None
    updated_time: datetime | None = None
    # Settlement.
    result: str | None = None
    expiration_value: str | None = None
    can_close_early: bool | None = None
    settlement_ts: datetime | None = None
    settlement_value_dollars: Decimal | None = None
    # Economics (fixed-point strings → Decimal).
    notional_value_dollars: Decimal | None = None
    last_price_dollars: Decimal | None = None
    previous_price_dollars: Decimal | None = None
    yes_bid_dollars: Decimal | None = None
    yes_ask_dollars: Decimal | None = None
    no_bid_dollars: Decimal | None = None
    no_ask_dollars: Decimal | None = None
    previous_yes_bid_dollars: Decimal | None = None
    previous_yes_ask_dollars: Decimal | None = None
    liquidity_dollars: Decimal | None = None
    volume_fp: Decimal | None = None
    volume_24h_fp: Decimal | None = None
    open_interest_fp: Decimal | None = None
    yes_bid_size_fp: Decimal | None = None
    yes_ask_size_fp: Decimal | None = None


# ---------------------------------------------------------------------------
# Market data objects
# ---------------------------------------------------------------------------


class Trade(KalshiModel):
    """One row of ``GET /markets/trades``. (``taker_side`` is deprecated.)"""

    trade_id: str
    ticker: str
    created_time: datetime
    count_fp: Decimal
    yes_price_dollars: Decimal
    no_price_dollars: Decimal
    taker_outcome_side: str | None = None
    taker_book_side: str | None = None
    is_block_trade: bool | None = None


class PriceOhlc(KalshiModel):
    """Nested OHLC object on a candlestick.

    Every field is optional: a period with no trades serves ``price`` with
    only ``previous_dollars`` (observed live), and the bid/ask objects may
    omit fields the same way.
    """

    open_dollars: Decimal | None = None
    high_dollars: Decimal | None = None
    low_dollars: Decimal | None = None
    close_dollars: Decimal | None = None
    previous_dollars: Decimal | None = None


class Candlestick(KalshiModel):
    """One candle; ``end_period_ts`` is served as Unix seconds."""

    end_period_ts: datetime
    yes_bid: PriceOhlc
    yes_ask: PriceOhlc
    price: PriceOhlc
    volume_fp: Decimal
    open_interest_fp: Decimal | None = None


class HistoricalCutoff(KalshiModel):
    """``GET /historical/cutoff`` — the moving live/historical boundary."""

    market_settled_ts: datetime
    trades_created_ts: datetime
    orders_updated_ts: datetime | None = None
    market_positions_last_updated_ts: datetime | None = None


# ---------------------------------------------------------------------------
# Response wrappers
# ---------------------------------------------------------------------------


class SeriesListResponse(KalshiModel):
    series: list[Series]


class SeriesResponse(KalshiModel):
    series: Series


class EventsPage(KalshiModel):
    events: list[Event]
    cursor: str | None = None


class EventResponse(KalshiModel):
    event: Event
    markets: list[Market] | None = None


class MarketsPage(KalshiModel):
    markets: list[Market]
    cursor: str | None = None


class MarketResponse(KalshiModel):
    market: Market


class TradesPage(KalshiModel):
    trades: list[Trade]
    cursor: str | None = None


class CandlesticksResponse(KalshiModel):
    candlesticks: list[Candlestick]
    ticker: str | None = None
