"""Unit tests for TickEventType StrEnum."""

from __future__ import annotations

import pytest

from manta_trading.data.base.tick_schema import TickEventType


class TestTickEventTypeMembers:
    """Verify enum membership and values."""

    def test_has_exactly_two_members(self) -> None:
        assert len(TickEventType) == 2

    def test_has_trade_member(self) -> None:
        assert TickEventType.TRADE in TickEventType

    def test_has_quote_member(self) -> None:
        assert TickEventType.QUOTE in TickEventType

    def test_trade_value(self) -> None:
        assert TickEventType.TRADE == "trade"

    def test_quote_value(self) -> None:
        assert TickEventType.QUOTE == "quote"


class TestTickEventTypeStrEnumBehavior:
    """Verify StrEnum string comparison and construction."""

    def test_trade_string_comparison(self) -> None:
        assert TickEventType.TRADE == "trade"

    def test_quote_string_comparison(self) -> None:
        assert TickEventType.QUOTE == "quote"

    def test_construct_trade_from_string(self) -> None:
        assert TickEventType("trade") is TickEventType.TRADE

    def test_construct_quote_from_string(self) -> None:
        assert TickEventType("quote") is TickEventType.QUOTE

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            TickEventType("invalid")
