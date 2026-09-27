"""``TickRequest``: immutable, exclusive end, ``with_schema`` (slice 220, TD 8)."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema
from manta_trading.data.tick.provider import TickRequest


def _request(
    start: date = date(2025, 1, 6), end: date = date(2025, 1, 11)
) -> TickRequest:
    return TickRequest(
        dataset=CME_DATASET,
        symbols=("ES.c.0",),
        stype_in=SType.CONTINUOUS,
        schema=TickSchema.TRADES,
        start=start,
        end=end,
    )


def test_with_schema_changes_only_the_schema() -> None:
    trades = _request()
    tbbo = trades.with_schema(TickSchema.TBBO)
    assert tbbo.schema is TickSchema.TBBO
    assert dataclasses.replace(tbbo, schema=TickSchema.TRADES) == trades
    assert trades.schema is TickSchema.TRADES


@pytest.mark.parametrize("end", [date(2025, 1, 6), date(2025, 1, 5)])
def test_end_not_after_start_raises(end: date) -> None:
    with pytest.raises(ValueError, match="exclusive"):
        _request(end=end)


def test_one_day_request_is_valid() -> None:
    assert _request(end=date(2025, 1, 7)).end == date(2025, 1, 7)


def test_no_symbols_raises() -> None:
    with pytest.raises(ValueError, match="at least one symbol"):
        dataclasses.replace(_request(), symbols=())


def test_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _request().schema = TickSchema.TBBO  # type: ignore[misc]
