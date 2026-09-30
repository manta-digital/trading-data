"""The configured tick universe: what the pass is asked to keep in hand (224).

LLD 224 Technical Decision 3. A code constant beside
``FUTURES_PRODUCT_CALENDAR`` (which 231 edits to add GC), validated when this
module is imported: a bad universe fails every ``mt`` command that touches it,
with the field named.

``tier=None`` is the honest state before slice 226's go/no-go: an entry with
no tier produces no tier wants (the pass still buys definitions for tier days
the manifest already owns). ``--start/--end`` on ``pass`` only narrow the
configured range.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from manta_trading.data.tick.constants import (
    FUTURES_PRODUCT_CALENDAR,
    STORED_TIERS,
    SType,
    TickSchema,
)


@dataclass(frozen=True)
class TickUniverseEntry:
    """One product's standing request shape."""

    product: str  # key into FUTURES_PRODUCT_CALENDAR
    symbols: tuple[str, ...]  # stored sorted
    stype_in: SType
    tier: TickSchema | None  # a STORED_TIERS member, or None = not chosen yet
    start: date | None  # wanted range, UTC days, end exclusive
    end: date | None  # None = up to the availability edge


# ES.FUT (parent) covers the outrights and the calendar spreads. Including the
# spreads is an explicit configuration, not the default: both adopted jobs were
# bought this way and hold spread trades (2.35% of the trades job's records), so
# other symbols would leave no adopted day covered. 226's go/no-go re-confirms
# it (TD3, review F004).
TICK_UNIVERSE: tuple[TickUniverseEntry, ...] = (
    TickUniverseEntry("ES", ("ES.FUT",), SType.PARENT, tier=None, start=None, end=None),
)


def _validate_entry(entry: TickUniverseEntry) -> None:
    label = f"tick universe entry {entry.product!r}"
    if entry.product not in FUTURES_PRODUCT_CALENDAR:
        known = ", ".join(sorted(FUTURES_PRODUCT_CALENDAR))
        raise ValueError(f"{label}: product has no trading calendar (known: {known})")
    if not entry.symbols:
        raise ValueError(f"{label}: symbols is empty")
    if list(entry.symbols) != sorted(entry.symbols):
        raise ValueError(f"{label}: symbols must be sorted, got {entry.symbols}")
    if entry.tier is not None and entry.tier not in STORED_TIERS:
        raise ValueError(f"{label}: tier {entry.tier} is not a stored tier")
    if entry.tier is not None and entry.start is None:
        raise ValueError(f"{label}: start is required when a tier is set")
    if entry.start is not None and entry.end is not None and entry.end <= entry.start:
        raise ValueError(f"{label}: end {entry.end} must be after start {entry.start}")


def validate_universe(entries: Sequence[TickUniverseEntry]) -> None:
    """Raise ``ValueError`` naming the field of the first invalid entry."""
    for entry in entries:
        _validate_entry(entry)
    products = [entry.product for entry in entries]
    duplicated = sorted({p for p in products if products.count(p) > 1})
    if duplicated:
        raise ValueError(f"tick universe: product listed twice: {duplicated}")


validate_universe(TICK_UNIVERSE)
