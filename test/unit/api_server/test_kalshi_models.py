"""Unit tests for the Kalshi response models (slice 188, Section 3).

The load-bearing test here is the column-parity one: a column added to
``MarketRow`` must appear somewhere in ``MarketRecord``, or it is read from
the database and then silently dropped on the way out.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, get_args, get_origin

import pytest

from manta_trading.api_server.models.kalshi import (
    MARKET_GROUPS,
    EventRecord,
    MarketRecord,
    SeriesRecord,
)
from manta_trading.data.kalshi.serve_catalog import EventRow, MarketRow, SeriesRow

TS = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _populated_market_row() -> MarketRow:
    """A ``MarketRow`` with every field set to a distinguishable value.

    Values are derived per field so the parity test can assert that each one
    arrives intact rather than merely that the shapes line up.
    """
    values: dict[str, Any] = {}
    for index, field in enumerate(MarketRow.__dataclass_fields__.values()):
        annotation = field.type
        text = annotation if isinstance(annotation, str) else str(annotation)
        if "datetime" in text:
            values[field.name] = datetime(2026, 6, 1, 12, 0, index % 60, tzinfo=UTC)
        elif "Decimal" in text:
            values[field.name] = Decimal(f"{index}.0000")
        elif "bool" in text:
            values[field.name] = index % 2 == 0
        else:
            values[field.name] = f"{field.name}-{index}"
    return MarketRow(**values)


def _unsettled_market_row() -> MarketRow:
    settlement_fields = MARKET_GROUPS["settlement"].model_fields
    return MarketRow(
        **{
            **vars(_populated_market_row()),
            **dict.fromkeys(settlement_fields),
        }
    )


class TestMarketColumnParity:
    def test_every_reader_column_is_reachable_exactly_once(self) -> None:
        """A column added to the reader cannot silently go unserved."""
        reader_columns = set(MarketRow.__dataclass_fields__)
        flat = set(MarketRecord.model_fields) - set(MARKET_GROUPS)
        nested = [
            field for model in MARKET_GROUPS.values() for field in model.model_fields
        ]

        assert len(nested) == len(set(nested)), "a column appears in two groups"
        assert flat.isdisjoint(nested), "a column is both flat and nested"
        assert flat | set(nested) == reader_columns

    def test_every_value_survives_the_mapping(self) -> None:
        row = _populated_market_row()
        record = MarketRecord.from_row(row)
        dumped = record.model_dump()
        for name, expected in vars(row).items():
            if name in dumped and name not in MARKET_GROUPS:
                assert dumped[name] == expected
                continue
            group = next(
                (
                    key
                    for key, model in MARKET_GROUPS.items()
                    if name in model.model_fields
                ),
                None,
            )
            assert group is not None, f"column {name!r} is served nowhere"
            assert dumped[group][name] == expected


class TestMarketRecord:
    def test_nested_objects_carry_their_columns(self) -> None:
        record = MarketRecord.from_row(_populated_market_row())
        assert record.lifecycle.close_time is not None
        assert record.economics.yes_bid_dollars is not None
        assert record.settlement.result is not None

    def test_unsettled_market_has_settlement_with_five_nulls(self) -> None:
        """``settlement`` is present on every market, null until settled (D3)."""
        record = MarketRecord.from_row(_unsettled_market_row())
        settlement = record.model_dump()["settlement"]
        assert len(settlement) == 5
        assert all(value is None for value in settlement.values())

    def test_raw_is_not_served(self) -> None:
        assert "raw" not in MarketRecord.model_fields
        assert "raw" not in MarketRow.__dataclass_fields__


class TestDecimalRendering:
    def test_decimal_dumps_as_its_exact_string(self) -> None:
        row = MarketRow(
            **{
                **vars(_populated_market_row()),
                "yes_bid_dollars": Decimal("0.4900"),
            }
        )
        record = MarketRecord.from_row(row)
        dumped = record.model_dump(mode="json")
        assert dumped["economics"]["yes_bid_dollars"] == "0.4900"

    @pytest.mark.parametrize("model", list(MARKET_GROUPS.values()))
    def test_no_group_types_a_money_column_as_float(self, model: Any) -> None:
        """Floats would undo the exactness the NUMERIC columns preserve (D7)."""
        for name, field in model.model_fields.items():
            args = get_args(field.annotation) or (field.annotation,)
            assert float not in args, f"{name} is typed float"
            assert get_origin(field.annotation) is not float


class TestSeriesAndEventRecords:
    def test_series_record_covers_every_reader_column(self) -> None:
        assert set(SeriesRecord.model_fields) == set(SeriesRow.__dataclass_fields__)

    def test_event_record_covers_every_reader_column(self) -> None:
        assert set(EventRecord.model_fields) == set(EventRow.__dataclass_fields__)

    def test_series_round_trips(self) -> None:
        row = SeriesRow(
            ticker="KXFED",
            frequency="monthly",
            title="Fed",
            category="Economics",
            tags=["a"],
            settlement_sources=None,
            fee_type="quadratic",
            fee_multiplier=Decimal("0.0700"),
            contract_url=None,
            contract_terms_url=None,
            product_metadata=None,
            last_updated_ts=TS,
            first_seen_at=TS,
            last_synced_at=TS,
        )
        dumped = SeriesRecord.from_row(row).model_dump(mode="json")
        assert dumped["ticker"] == "KXFED"
        assert dumped["fee_multiplier"] == "0.0700"
