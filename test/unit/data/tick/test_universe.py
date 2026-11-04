"""Universe validation (slice 224, LLD Technical Decision 3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from manta_trading.data.tick.constants import (
    STORED_TIERS,
    SType,
    TickSchema,
    calendar_for_product,
)
from manta_trading.data.tick.universe import (
    TICK_UNIVERSE,
    HistoryWant,
    TickUniverseEntry,
    validate_universe,
)
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START
from manta_trading.market.schema.seed_cme_metals_calendar import (
    CME_METALS_CALENDAR_ID,
)

GOOD = TickUniverseEntry(
    "ES",
    ("ES.FUT",),
    SType.PARENT,
    tier=TickSchema.TRADES,
    start=date(2024, 1, 1),
    end=date(2024, 2, 1),
)


def test_the_shipped_universe_validates_with_a_bounded_tier_range() -> None:
    """226's go/no-go set ES's tier and the range held at it; the values are
    the PM's to change, so this checks their shape, not the values."""
    validate_universe(TICK_UNIVERSE)
    assert [e.product for e in TICK_UNIVERSE] == ["ES", "GC"]
    es = TICK_UNIVERSE[0]
    assert es.tier in STORED_TIERS
    assert es.start is not None and es.end is not None and es.start < es.end
    assert TICK_UNIVERSE[0].symbols == ("ES.FUT",)
    assert TICK_UNIVERSE[0].stype_in is SType.PARENT


def test_gc_entry_matches_td4() -> None:
    """Slice 232 TD4: GC.FUT parent at tbbo over ES's range, on CME_METALS."""
    gc = next(e for e in TICK_UNIVERSE if e.product == "GC")
    assert gc.symbols == ("GC.FUT",)
    assert gc.stype_in is SType.PARENT
    assert gc.tier is TickSchema.TBBO
    assert (gc.start, gc.end) == (date(2024, 11, 2), date(2025, 1, 1))
    assert calendar_for_product("GC") == CME_METALS_CALENDAR_ID


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


# -- history wants (slice 302 D5) ---------------------------------------------

BARS = HistoryWant(TickSchema.OHLCV_1M, date(2012, 1, 1), date(2013, 1, 1))
DEFINITIONS = HistoryWant(TickSchema.DEFINITION, date(2011, 12, 1), date(2013, 2, 1))
WITH_HISTORY = replace(GOOD, history=(DEFINITIONS, BARS))


def test_production_universe_has_no_history() -> None:
    assert all(entry.history == () for entry in TICK_UNIVERSE)


def test_an_entry_with_both_history_wants_validates() -> None:
    validate_universe([WITH_HISTORY])
    validate_universe([replace(WITH_HISTORY, history=(DEFINITIONS,))])
    open_ended = (
        replace(DEFINITIONS, end=None),
        replace(BARS, end=None),
    )
    validate_universe([replace(GOOD, history=open_ended)])


def _rejects(entry: TickUniverseEntry, *needles: str) -> None:
    with pytest.raises(ValueError) as raised:
        validate_universe([entry])
    message = str(raised.value)
    assert "'ES'" in message  # the entry
    for needle in needles:
        assert needle in message


def test_a_bar_want_outside_the_definition_range_is_rejected() -> None:
    bars = replace(BARS, end=date(2013, 3, 1))
    _rejects(
        replace(GOOD, history=(DEFINITIONS, bars)), "history[ohlcv-1m]", "not inside"
    )
    early = replace(BARS, start=date(2011, 6, 1))
    _rejects(
        replace(GOOD, history=(DEFINITIONS, early)), "history[ohlcv-1m]", "not inside"
    )


def test_a_bar_want_with_no_definition_want_is_rejected() -> None:
    _rejects(replace(GOOD, history=(BARS,)), "history[ohlcv-1m]", "needs a definition")


def test_a_start_before_the_dataset_start_is_rejected() -> None:
    early = date(2010, 6, 5)
    assert early < CME_DATASET_START
    wants = (replace(DEFINITIONS, start=early), replace(BARS, start=early))
    _rejects(replace(GOOD, history=wants), "history[definition].start", str(early))


def test_a_repeated_schema_is_rejected() -> None:
    _rejects(replace(GOOD, history=(DEFINITIONS, DEFINITIONS)), "more than once")


def test_a_history_want_on_an_entry_with_no_tier_is_rejected() -> None:
    _rejects(replace(WITH_HISTORY, tier=None), "tier", "not chosen")


@pytest.mark.parametrize(
    "schema", [TickSchema.TBBO, TickSchema.MBP_1, TickSchema.TRADES]
)
def test_a_schema_other_than_the_two_allowed_is_rejected(schema: TickSchema) -> None:
    other = HistoryWant(schema, date(2012, 1, 1), date(2013, 1, 1))
    _rejects(replace(GOOD, history=(other,)), f"history[{schema}].schema")


def test_an_open_bar_end_with_a_closed_definition_end_is_rejected() -> None:
    open_bars = replace(BARS, end=None)
    _rejects(replace(GOOD, history=(DEFINITIONS, open_bars)), "not inside")


def test_an_inverted_want_range_is_rejected() -> None:
    inverted = replace(DEFINITIONS, end=DEFINITIONS.start)
    _rejects(replace(GOOD, history=(inverted,)), "history[definition].end")


# -- statistics wants (slice 303 D6) ------------------------------------------

STATISTICS = HistoryWant(TickSchema.STATISTICS, date(2012, 1, 1), date(2013, 1, 1))


def test_an_entry_with_a_statistics_want_validates() -> None:
    validate_universe([replace(GOOD, history=(DEFINITIONS, STATISTICS))])
    validate_universe([replace(GOOD, history=(DEFINITIONS, BARS, STATISTICS))])
    open_ended = (replace(DEFINITIONS, end=None), replace(STATISTICS, end=None))
    validate_universe([replace(GOOD, history=open_ended)])


def test_a_statistics_want_outside_the_definition_range_is_rejected() -> None:
    late = replace(STATISTICS, end=date(2013, 3, 1))
    _rejects(
        replace(GOOD, history=(DEFINITIONS, late)), "history[statistics]", "not inside"
    )
    early = replace(STATISTICS, start=date(2011, 6, 1))
    _rejects(
        replace(GOOD, history=(DEFINITIONS, early)), "history[statistics]", "not inside"
    )


def test_a_statistics_want_with_no_definition_want_is_rejected() -> None:
    _rejects(
        replace(GOOD, history=(STATISTICS,)),
        "history[statistics]",
        "needs a definition",
    )


def test_a_statistics_start_before_the_dataset_start_is_rejected() -> None:
    early = date(2010, 6, 5)
    wants = (replace(DEFINITIONS, start=early), replace(STATISTICS, start=early))
    _rejects(replace(GOOD, history=wants), "history[definition].start", str(early))
    _rejects(
        replace(GOOD, history=(DEFINITIONS, replace(STATISTICS, start=early))),
        "history[statistics].start",
    )


def test_a_repeated_statistics_want_is_rejected() -> None:
    _rejects(
        replace(GOOD, history=(DEFINITIONS, STATISTICS, STATISTICS)),
        "history lists statistics more than once",
    )


def test_a_statistics_want_on_an_entry_with_no_tier_is_rejected() -> None:
    _rejects(
        replace(GOOD, tier=None, history=(DEFINITIONS, STATISTICS)),
        "tier",
        "not chosen",
    )


def test_the_production_universe_wants_no_statistics() -> None:
    assert all(
        want.schema is not TickSchema.STATISTICS
        for entry in TICK_UNIVERSE
        for want in entry.history
    )
