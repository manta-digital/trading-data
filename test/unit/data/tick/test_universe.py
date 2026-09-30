"""Universe validation (slice 224, LLD Technical Decision 3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from manta_trading.data.tick.constants import SType, TickSchema
from manta_trading.data.tick.universe import (
    TICK_UNIVERSE,
    TickUniverseEntry,
    validate_universe,
)

GOOD = TickUniverseEntry(
    "ES",
    ("ES.FUT",),
    SType.PARENT,
    tier=TickSchema.TRADES,
    start=date(2024, 1, 1),
    end=date(2024, 2, 1),
)


def test_the_shipped_universe_validates_and_has_no_tier() -> None:
    validate_universe(TICK_UNIVERSE)
    assert [e.product for e in TICK_UNIVERSE] == ["ES"]
    assert TICK_UNIVERSE[0].tier is None
    assert TICK_UNIVERSE[0].symbols == ("ES.FUT",)
    assert TICK_UNIVERSE[0].stype_in is SType.PARENT


def test_a_well_formed_entry_validates() -> None:
    validate_universe([GOOD])
    validate_universe([replace(GOOD, tier=None, start=None, end=None)])
    validate_universe([replace(GOOD, end=None)])


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"product": "ZZ"}, "product"),
        ({"symbols": ()}, "symbols"),
        ({"symbols": ("ES.c.1", "ES.c.0")}, "sorted"),
        ({"tier": TickSchema.MBP_1}, "tier"),
        ({"tier": TickSchema.DEFINITION}, "tier"),
        ({"start": None}, "start"),
        ({"end": date(2024, 1, 1)}, "end"),
        ({"end": date(2023, 12, 1)}, "end"),
    ],
)
def test_each_bad_entry_names_its_field(changes: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        validate_universe([replace(GOOD, **changes)])


def test_duplicate_products_are_refused() -> None:
    with pytest.raises(ValueError, match="listed twice"):
        validate_universe([GOOD, GOOD])
