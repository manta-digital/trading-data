"""An explicit untiered universe for tests of the no-tier path (slice 226).

``TICK_UNIVERSE`` is production configuration: since 226 it names ES's tier
and range. Tests written for the pass's mechanics with no tier chosen use
this instead, so a configuration edit never changes what they test. That
includes the history wants slice 304 added: the untiered universe has none.
``WITH_HISTORY`` is the reverse: production's entries with the history wants
they carried until slice 309 removed them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import ModuleType

import pytest

from manta_trading.data.tick import acquisition_pass, purchase_plan
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.universe import (
    TICK_UNIVERSE,
    HistoryWant,
    TickUniverseEntry,
)

UNTIERED: tuple[TickUniverseEntry, ...] = tuple(
    replace(entry, tier=None, start=None, end=None, history=())
    for entry in TICK_UNIVERSE
)


def use_untiered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the pass's two readers of the universe at ``UNTIERED``."""
    monkeypatch.setattr(acquisition_pass, "TICK_UNIVERSE", UNTIERED)
    monkeypatch.setattr(purchase_plan, "TICK_UNIVERSE", UNTIERED)


def use_only(monkeypatch: pytest.MonkeyPatch, product: str) -> None:
    """Point the pass's two readers of the universe at one shipped entry, for
    tests seeded with that product's history alone (232 added GC), without the
    history wants of 304 (they test the tbbo tier)."""
    only = tuple(
        replace(entry, history=())
        for entry in TICK_UNIVERSE
        if entry.product == product
    )
    monkeypatch.setattr(acquisition_pass, "TICK_UNIVERSE", only)
    monkeypatch.setattr(purchase_plan, "TICK_UNIVERSE", only)


#: The history wants production carried from slice 304 to 309 (definition,
#: statistics and ``ohlcv-1m`` from 2010-06-08 to 2026-11-10). Production wants
#: none since 309; tests of the history mechanism and the purchase scripts use
#: these, so a configuration edit never changes what they test.
HISTORY_START, HISTORY_END = date(2010, 6, 8), date(2026, 11, 10)
HISTORY_WANTS: tuple[HistoryWant, ...] = tuple(
    HistoryWant(schema, HISTORY_START, HISTORY_END)
    for schema in (TickSchema.DEFINITION, TickSchema.STATISTICS, TickSchema.OHLCV_1M)
)
WITH_HISTORY: tuple[TickUniverseEntry, ...] = tuple(
    replace(entry, history=HISTORY_WANTS) for entry in TICK_UNIVERSE
)


def use_history(monkeypatch: pytest.MonkeyPatch, *modules: ModuleType) -> None:
    """Point each module's ``TICK_UNIVERSE`` at ``WITH_HISTORY``."""
    for module in modules:
        monkeypatch.setattr(module, "TICK_UNIVERSE", WITH_HISTORY)
