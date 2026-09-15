"""Unit tests for the Kalshi response models (slice 188, Section 3).

The load-bearing test here is the column-parity one: a column added to
``MarketRow`` must appear somewhere in ``MarketRecord``, or it is read from
the database and then silently dropped on the way out.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, get_args, get_origin
from uuid import UUID

import pytest

from manta_trading.api_server.models.kalshi_catalog import (
    MARKET_GROUPS,
    EventRecord,
    MarketRecord,
    SeriesRecord,
)
from manta_trading.api_server.models.kalshi_timeseries import (
    CandleRecord,
    CandlesResponse,
    TradeRecord,
    TradesResponse,
)
from manta_trading.data.kalshi.candle_repository import CANDLE_COLUMNS
from manta_trading.data.kalshi.serve_catalog import EventRow, MarketRow, SeriesRow
from manta_trading.data.kalshi.serve_timeseries import (
    TRADE_VALUE_COLUMNS,
    CandleRow,
    MarketContext,
    TapeFacts,
    TradeRow,
)

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


# ---------------------------------------------------------------------------
# Section 5 — time-series models
# ---------------------------------------------------------------------------


def _candle_row(values: tuple[Decimal | None, ...] | None = None) -> CandleRow:
    if values is None:
        values = tuple(Decimal(f"{i}.0000") for i in range(len(CANDLE_COLUMNS)))
    return CandleRow(end_period_ts=TS, values=values)


class TestCandleRecord:
    def test_every_stored_column_lands_in_its_documented_slot(self) -> None:
        """The fourteen values re-nest exactly as CANDLE_COLUMNS describes."""
        values = tuple(Decimal(f"{i}.0000") for i in range(len(CANDLE_COLUMNS)))
        dumped = CandleRecord.from_row(_candle_row(values)).model_dump()

        for value, (_, path) in zip(values, CANDLE_COLUMNS, strict=True):
            target = dumped
            for step in path:
                target = target[step]
            assert target == value

    def test_nested_field_set_is_exactly_what_candle_columns_describes(self) -> None:
        """A column added to the repository mapping cannot go unserved."""
        dumped = CandleRecord.from_row(_candle_row()).model_dump()
        reachable = {
            (path[0],) if len(path) == 1 else (path[0], path[1])
            for _, path in CANDLE_COLUMNS
        }
        served: set[tuple[str, ...]] = set()
        for key, value in dumped.items():
            if key == "end_period_ts":
                continue
            if isinstance(value, dict):
                served |= {(key, inner) for inner in value}
            else:
                served.add((key,))
        assert served == reachable

    def test_a_sparse_row_keeps_its_nulls_and_its_shape(self) -> None:
        """A period with no trades carries previous_dollars alone — the other
        fields stay null rather than the object being dropped."""
        columns = [name for name, _ in CANDLE_COLUMNS]
        values = tuple(
            Decimal("0.4900") if name == "price_previous_dollars" else None
            for name in columns
        )
        dumped = CandleRecord.from_row(_candle_row(values)).model_dump()

        assert dumped["price"]["previous_dollars"] == Decimal("0.4900")
        assert dumped["price"]["open_dollars"] is None
        assert dumped["yes_bid"] == dict.fromkeys(
            ["open_dollars", "high_dollars", "low_dollars", "close_dollars"]
        )
        assert dumped["volume_fp"] is None

    def test_decimals_dump_as_strings(self) -> None:
        columns = [name for name, _ in CANDLE_COLUMNS]
        values = tuple(
            Decimal("0.4900") if name == "yes_bid_open_dollars" else None
            for name in columns
        )
        dumped = CandleRecord.from_row(_candle_row(values)).model_dump(mode="json")
        assert dumped["yes_bid"]["open_dollars"] == "0.4900"


class TestCandlesResponse:
    def test_reports_the_context_facts(self) -> None:
        context = MarketContext(
            ticker="KXFED-26SEP-T1",
            series_category="Economics",
            candle_collected=True,
            candle_coverage_from=TS,
            candle_complete_through=TS,
        )
        response = CandlesResponse.build(context, 1, [_candle_row()])
        dumped = response.model_dump(mode="json")

        assert dumped["market_ticker"] == "KXFED-26SEP-T1"
        assert dumped["period_minutes"] == 1
        assert dumped["collected"] is True
        assert dumped["count"] == 1

    def test_uncollected_market_reports_nulls_not_an_empty_window(self) -> None:
        """``collected: false`` is distinguishable from a quiet window (D5)."""
        context = MarketContext(
            ticker="QUIET",
            series_category=None,
            candle_collected=False,
            candle_coverage_from=None,
            candle_complete_through=None,
        )
        dumped = CandlesResponse.build(context, 1, []).model_dump(mode="json")

        assert dumped["collected"] is False
        assert dumped["coverage_from"] is None
        assert dumped["complete_through"] is None
        assert dumped["count"] == 0


class TestTradesResponse:
    def test_absent_tape_facts_serialize_as_nulls(self) -> None:
        """The trades phase has never run — a 200 with nulls, not an error."""
        dumped = TradesResponse.build(
            "KXFED-26SEP-T1", None, filtered=False, rows=[]
        ).model_dump(mode="json")

        assert dumped["count"] == 0
        assert dumped["coverage_from"] is None
        assert dumped["tape_complete_through"] is None
        assert dumped["tape_filtered"] is False
        assert dumped["trades"] == []

    def test_trade_record_serializes_its_decimals_as_strings(self) -> None:
        row = TradeRow(
            values=(
                TS,
                UUID("4b1d1e2a-0000-4000-8000-000000000001"),
                Decimal("5.00"),
                Decimal("0.4900"),
                Decimal("0.5100"),
                "yes",
                "yes",
                False,
            )
        )
        facts = TapeFacts(coverage_from=TS, tape_complete_through=TS)
        dumped = TradesResponse.build(
            "KXFED-26SEP-T1", facts, filtered=False, rows=[row]
        ).model_dump(mode="json")

        trade = dumped["trades"][0]
        assert trade["yes_price_dollars"] == "0.4900"
        assert trade["count_fp"] == "5.00"
        assert trade["trade_id"] == "4b1d1e2a-0000-4000-8000-000000000001"
        assert trade["is_block_trade"] is False

    def test_filtered_tape_is_reported_as_a_fact(self) -> None:
        dumped = TradesResponse.build(
            "KXSPORTS-1", None, filtered=True, rows=[]
        ).model_dump(mode="json")
        assert dumped["tape_filtered"] is True


class TestTradeRecordMapsByName:
    """`TradeRecord.from_row` must follow the column mapping, not positions.

    188 code review F001. The values arrive in the order the SELECT asked for
    them — `TRADE_VALUE_COLUMNS`, derived from the repository's
    `TRADE_COLUMNS`. Reading them by literal index worked only because those
    orders happened to agree, with nothing tying them together, and the suite
    checked the column-name *set* rather than its order. A column inserted or
    reordered upstream would have silently misassigned every field after it.
    """

    @staticmethod
    def _values() -> tuple[Any, ...]:
        """One row's values, built by NAME so this helper cannot itself bake
        in the positional assumption under test."""
        by_name: dict[str, Any] = {
            "created_time": datetime(2026, 2, 1, 12, 30, tzinfo=UTC),
            "trade_id": UUID("0786aa54-6fe2-5bd0-de6e-ed09ebd15e7e"),
            "count_fp": Decimal("1167.00"),
            "yes_price_dollars": Decimal("0.0500"),
            "no_price_dollars": Decimal("0.9500"),
            "taker_outcome_side": "no",
            "taker_book_side": "ask",
            "is_block_trade": False,
        }
        return tuple(by_name[name] for name in TRADE_VALUE_COLUMNS)

    def test_every_field_lands_where_it_belongs(self) -> None:
        record = TradeRecord.from_row(TradeRow(values=self._values()))

        assert record.created_time == datetime(2026, 2, 1, 12, 30, tzinfo=UTC)
        assert record.trade_id == "0786aa54-6fe2-5bd0-de6e-ed09ebd15e7e"
        assert record.count_fp == Decimal("1167.00")
        assert record.yes_price_dollars == Decimal("0.0500")
        assert record.no_price_dollars == Decimal("0.9500")
        assert record.taker_outcome_side == "no"
        assert record.taker_book_side == "ask"
        assert record.is_block_trade is False

    def test_a_reordered_column_mapping_does_not_misassign(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The assertion the old positional code could not satisfy.

        Swap two columns in the mapping, feed values in that new order, and
        the record must still be correct. Positional indices would silently
        transpose the two fields and every assertion below would still have
        passed on the old code for the *unswapped* pair — which is exactly why
        a reorder was invisible.
        """
        reordered = list(TRADE_VALUE_COLUMNS)
        i, j = reordered.index("taker_outcome_side"), reordered.index("taker_book_side")
        reordered[i], reordered[j] = reordered[j], reordered[i]
        monkeypatch.setattr(
            "manta_trading.api_server.models.kalshi_timeseries.TRADE_VALUE_COLUMNS",
            tuple(reordered),
        )

        by_name: dict[str, Any] = {
            "created_time": datetime(2026, 2, 1, 12, 30, tzinfo=UTC),
            "trade_id": UUID("0786aa54-6fe2-5bd0-de6e-ed09ebd15e7e"),
            "count_fp": Decimal("1167.00"),
            "yes_price_dollars": Decimal("0.0500"),
            "no_price_dollars": Decimal("0.9500"),
            "taker_outcome_side": "no",
            "taker_book_side": "ask",
            "is_block_trade": False,
        }
        values = tuple(by_name[name] for name in reordered)

        record = TradeRecord.from_row(TradeRow(values=values))

        # Both still land correctly, because the mapping is followed.
        assert record.taker_outcome_side == "no"
        assert record.taker_book_side == "ask"

    def test_a_length_mismatch_raises_rather_than_dropping_a_column(self) -> None:
        """`strict=True`: a short row is a loud error, not a missing field."""
        with pytest.raises(ValueError, match="argument"):
            TradeRecord.from_row(TradeRow(values=self._values()[:-1]))

    def test_the_model_covers_every_served_column(self) -> None:
        """Model fields and served columns are the same set, in the same
        order — so neither can gain a column the other does not know about."""
        assert tuple(TradeRecord.model_fields) == TRADE_VALUE_COLUMNS
