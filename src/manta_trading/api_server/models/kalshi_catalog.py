"""Pydantic response models for the Kalshi catalog routes (slice 188).

Kept apart from ``responses.py`` (D12): the equity models there and these
share no field, and one module carrying both would be the larger part of a
reader's search space for either.

Decimal-typed columns are typed ``Decimal``, never ``float``, so the
serialization helper's ``mode="json"`` renders them as the exact strings the
``NUMERIC`` columns hold (D7).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic needs the runtime type
from decimal import Decimal  # noqa: TC003 — pydantic needs the runtime type
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from manta_trading.data.kalshi.serve_catalog import (
        CategoryCount,
        EventRow,
        MarketRow,
        SeriesRow,
    )


class CategoryRecord(BaseModel):
    """One series category and how many series carry it."""

    category: str | None
    series_count: int

    @classmethod
    def from_row(cls, row: CategoryCount) -> CategoryRecord:
        return cls(category=row.category, series_count=row.series_count)


class CategoryListResponse(BaseModel):
    """Every distinct series category.

    ``count`` is the number of categories, not the number of series; the
    per-category series totals are on the records themselves.
    """

    count: int
    categories: list[CategoryRecord]

    @classmethod
    def from_rows(cls, rows: list[CategoryCount]) -> CategoryListResponse:
        records = [CategoryRecord.from_row(row) for row in rows]
        return cls(count=len(records), categories=records)


class SeriesRecord(BaseModel):
    """A Kalshi series. Field names are the Kalshi/DB names verbatim."""

    ticker: str
    frequency: str | None
    title: str | None
    category: str | None
    tags: Any
    settlement_sources: Any
    fee_type: str | None
    fee_multiplier: Decimal | None
    contract_url: str | None
    contract_terms_url: str | None
    product_metadata: Any
    last_updated_ts: datetime | None
    first_seen_at: datetime
    last_synced_at: datetime

    @classmethod
    def from_row(cls, row: SeriesRow) -> SeriesRecord:
        return cls(**vars(row))


class SeriesListResponse(BaseModel):
    """Series matching the requested filter, complete — never truncated (D4)."""

    count: int
    series: list[SeriesRecord]

    @classmethod
    def from_rows(cls, rows: list[SeriesRow]) -> SeriesListResponse:
        records = [SeriesRecord.from_row(row) for row in rows]
        return cls(count=len(records), series=records)


class EventRecord(BaseModel):
    """A Kalshi event. Field names are the Kalshi/DB names verbatim."""

    event_ticker: str
    series_ticker: str
    title: str | None
    sub_title: str | None
    category: str | None
    mutually_exclusive: bool | None
    strike_date: datetime | None
    strike_period: str | None
    collateral_return_type: str | None
    available_on_brokers: bool | None
    settlement_sources: Any
    product_metadata: Any
    last_updated_ts: datetime | None
    first_seen_at: datetime
    last_synced_at: datetime

    @classmethod
    def from_row(cls, row: EventRow) -> EventRecord:
        return cls(**vars(row))


class EventListResponse(BaseModel):
    """Events of one series, complete — never truncated (D4)."""

    series_ticker: str
    count: int
    events: list[EventRecord]

    @classmethod
    def from_rows(cls, series_ticker: str, rows: list[EventRow]) -> EventListResponse:
        records = [EventRecord.from_row(row) for row in rows]
        return cls(series_ticker=series_ticker, count=len(records), events=records)


class MarketLifecycle(BaseModel):
    """Kalshi's own timestamps for a market's progression."""

    created_time: datetime | None
    open_time: datetime | None
    close_time: datetime
    expiration_time: datetime | None
    expected_expiration_time: datetime | None
    latest_expiration_time: datetime | None
    updated_time: datetime | None


class MarketSettlement(BaseModel):
    """How a market resolved.

    Present on **every** market, with all five fields null until the market
    settles (D3). An absent object and an unsettled market would otherwise be
    indistinguishable from a client's point of view.
    """

    result: str | None
    expiration_value: str | None
    can_close_early: bool | None
    settlement_ts: datetime | None
    settlement_value_dollars: Decimal | None


class MarketEconomics(BaseModel):
    """Prices, sizes and volumes, as fixed-point decimal strings (D7)."""

    notional_value_dollars: Decimal | None
    last_price_dollars: Decimal | None
    previous_price_dollars: Decimal | None
    yes_bid_dollars: Decimal | None
    yes_ask_dollars: Decimal | None
    no_bid_dollars: Decimal | None
    no_ask_dollars: Decimal | None
    liquidity_dollars: Decimal | None
    volume_fp: Decimal | None
    volume_24h_fp: Decimal | None
    open_interest_fp: Decimal | None
    yes_bid_size_fp: Decimal | None
    yes_ask_size_fp: Decimal | None
    previous_yes_bid_dollars: Decimal | None
    previous_yes_ask_dollars: Decimal | None


#: The flat-to-nested mapping, spelled once: each nested model and the
#: ``MarketRow`` columns it owns. ``MarketRecord.from_row`` builds the nested
#: objects from this, and ``test_kalshi_models`` asserts that every column is
#: reachable exactly once, so a column added to the reader cannot silently go
#: unserved.
MARKET_GROUPS: dict[str, type[BaseModel]] = {
    "lifecycle": MarketLifecycle,
    "settlement": MarketSettlement,
    "economics": MarketEconomics,
}

#: The ``MarketRow`` columns that live inside a nested object rather than on
#: ``MarketRecord`` itself — derived from ``MARKET_GROUPS``, never restated.
GROUPED_MARKET_COLUMNS = frozenset(
    field for model in MARKET_GROUPS.values() for field in model.model_fields
)


class MarketRecord(BaseModel):
    """A Kalshi market: every typed column except ``raw`` (D2).

    The lifecycle / settlement / economics grouping is for readability; the
    field names inside each group are the DB/Kalshi names verbatim, so
    Kalshi's own field documentation applies to them unchanged.
    """

    ticker: str
    event_ticker: str
    market_type: str | None
    status: str
    title: str | None
    subtitle: str | None
    yes_sub_title: str | None
    no_sub_title: str | None
    rules_primary: str | None
    rules_secondary: str | None
    strike_type: str | None
    price_level_structure: str | None
    is_provisional: bool | None
    mve_collection_ticker: str | None
    first_seen_at: datetime
    last_synced_at: datetime
    lifecycle: MarketLifecycle
    settlement: MarketSettlement
    economics: MarketEconomics

    @classmethod
    def from_row(cls, row: MarketRow) -> MarketRecord:
        flat = vars(row)

        def group(name: str) -> dict[str, Any]:
            model = MARKET_GROUPS[name]
            return {field: flat[field] for field in model.model_fields}

        return cls(
            **{k: v for k, v in flat.items() if k not in GROUPED_MARKET_COLUMNS},
            lifecycle=MarketLifecycle(**group("lifecycle")),
            settlement=MarketSettlement(**group("settlement")),
            economics=MarketEconomics(**group("economics")),
        )


class MarketListResponse(BaseModel):
    """Markets of one event, complete — never truncated (D4)."""

    event_ticker: str
    count: int
    markets: list[MarketRecord]

    @classmethod
    def from_rows(cls, event_ticker: str, rows: list[MarketRow]) -> MarketListResponse:
        records = [MarketRecord.from_row(row) for row in rows]
        return cls(event_ticker=event_ticker, count=len(records), markets=records)
