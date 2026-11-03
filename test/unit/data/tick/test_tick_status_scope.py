"""Which families an entry wants, and so which verdict sets status builds (302 D6)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from manta_trading.data.tick.constants import SType, TickSchema
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_status_scope import FamilyWant, family_wants
from manta_trading.data.tick.universe import HistoryWant, TickUniverseEntry

START, END = date(2024, 9, 2), date(2024, 9, 5)
TIERED = TickUniverseEntry("ES", ("ES.FUT",), SType.PARENT, TickSchema.TBBO, START, END)
DEFINITIONS = HistoryWant(TickSchema.DEFINITION, date(2012, 1, 1), date(2013, 1, 1))
BARS = HistoryWant(TickSchema.OHLCV_1M, date(2012, 2, 1), date(2012, 12, 1))


def test_an_entry_with_no_bar_want_produces_no_bar_verdicts() -> None:
    assert family_wants(TIERED) == [FamilyWant(SchemaFamily.TICK_TIER, START, END)]
    with_definitions = replace(TIERED, history=(DEFINITIONS,))
    assert [w.family for w in family_wants(with_definitions)] == [
        SchemaFamily.TICK_TIER
    ]


def test_an_entry_with_a_bar_want_adds_the_bar_family_over_its_own_range() -> None:
    entry = replace(TIERED, history=(DEFINITIONS, BARS))
    assert family_wants(entry) == [
        FamilyWant(SchemaFamily.TICK_TIER, START, END),
        FamilyWant(SchemaFamily.BAR_1M, BARS.start, BARS.end),
    ]


def test_an_entry_with_no_tier_wants_no_family() -> None:
    assert family_wants(replace(TIERED, tier=None, start=None, end=None)) == []
