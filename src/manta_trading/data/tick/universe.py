"""The configured tick universe: what the pass is asked to keep in hand (224).

LLD 224 Technical Decision 3. A code constant beside
``FUTURES_PRODUCT_CALENDAR`` (which 232 edits to add GC), validated when this
module is imported: a bad universe fails every ``mt`` command that touches it,
with the field named.

``tier=None`` means no tier is chosen: an entry with no tier produces no tier
wants (the pass still buys definitions for tier days the manifest already
owns). ES's tier and range were set by slice 226's go/no-go.
``--start/--end`` on ``pass`` only narrow the configured range.

``history`` (slice 302 D5) lists wants beyond the tick tier: ``ohlcv-1m`` bars
and the ``definition`` days they need. Production configures none.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from manta_trading.data.tick.constants import (
    FUTURES_PRODUCT_CALENDAR,
    HISTORY_SCHEMAS,
    STORED_TIERS,
    SType,
    TickSchema,
)
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START


@dataclass(frozen=True)
class HistoryWant:
    """One schema wanted over a range, on its entry's symbols and stype."""

    schema: TickSchema  # a HISTORY_SCHEMAS member
    start: date  # UTC days, end exclusive
    end: date | None = None  # None = up to the availability edge


@dataclass(frozen=True)
class TickUniverseEntry:
    """One product's standing request shape."""

    product: str  # key into FUTURES_PRODUCT_CALENDAR
    symbols: tuple[str, ...]  # stored sorted
    stype_in: SType
    tier: TickSchema | None  # a STORED_TIERS member, or None = not chosen yet
    start: date | None  # wanted range, UTC days, end exclusive
    end: date | None  # None = up to the availability edge
    history: tuple[HistoryWant, ...] = ()


# ES.FUT (parent) covers the outrights and the calendar spreads. Including the
# spreads is an explicit configuration, not the default: both adopted jobs were
# bought this way and hold spread trades, and 226 measured them at 2.3% of
# trades and 1.8% of tbbo rows (user/notes/2026-10-03-226-proof-size.md).
#
# Tier tbbo (226 go/no-go, user/analysis/226-analysis.tick-proof-go-no-go.md):
# the quote at every trade for 1.5x trades' compressed bytes per row. The range
# is the tbbo already held, so a pass buys nothing before a subscription;
# widening it to the plan year is this one edit. It starts 2024-11-02, not
# 11-01: the session dated 11-01 opens at 22:00 UTC on 10-31, a day the tbbo
# job does not hold, so the first session held whole is the one dated 11-04.
#
# GC (slice 232 TD4, TD5): ES's range, which holds GC's December -> February
# roll and the metals Thanksgiving closes, bought at tbbo. GC.FUT parent keeps
# the spreads, measured before purchase at 6.6% of GC's tbbo rows; a product
# drops its spreads only above a quarter of its rows.
TICK_UNIVERSE: tuple[TickUniverseEntry, ...] = (
    TickUniverseEntry(
        "ES",
        ("ES.FUT",),
        SType.PARENT,
        tier=TickSchema.TBBO,
        start=date(2024, 11, 2),
        end=date(2025, 1, 1),
    ),
    TickUniverseEntry(
        "GC",
        ("GC.FUT",),
        SType.PARENT,
        tier=TickSchema.TBBO,
        start=date(2024, 11, 2),
        end=date(2025, 1, 1),
    ),
)


def _validate_want(label: str, want: HistoryWant) -> None:
    field = f"history[{want.schema}]"
    if want.schema not in HISTORY_SCHEMAS:
        allowed = ", ".join(sorted(HISTORY_SCHEMAS))
        raise ValueError(f"{label}: {field}.schema is not a history schema ({allowed})")
    if want.start < CME_DATASET_START:
        raise ValueError(
            f"{label}: {field}.start {want.start} is before the CME dataset start"
            f" {CME_DATASET_START}"
        )
    if want.end is not None and want.end <= want.start:
        raise ValueError(
            f"{label}: {field}.end {want.end} must be after start {want.start}"
        )


def _validate_bars_inside_definitions(label: str, entry: TickUniverseEntry) -> None:
    """A bar day with no definition day would wait forever (D5)."""
    wants = {want.schema: want for want in entry.history}
    bars = wants.get(TickSchema.OHLCV_1M)
    if bars is None:
        return
    field = f"history[{TickSchema.OHLCV_1M}]"
    definitions = wants.get(TickSchema.DEFINITION)
    if definitions is None:
        raise ValueError(f"{label}: {field} needs a {TickSchema.DEFINITION} want")
    starts_before = bars.start < definitions.start
    ends_after = definitions.end is not None and (
        bars.end is None or bars.end > definitions.end
    )
    if starts_before or ends_after:
        raise ValueError(
            f"{label}: {field} range {bars.start}..{bars.end} is not inside the"
            f" {TickSchema.DEFINITION} want {definitions.start}..{definitions.end}"
        )


def _validate_history(label: str, entry: TickUniverseEntry) -> None:
    if not entry.history:
        return
    if entry.tier is None:
        raise ValueError(
            f"{label}: tier is required when history is set (None means not chosen)"
        )
    schemas = [want.schema for want in entry.history]
    for want in entry.history:
        if schemas.count(want.schema) > 1:
            raise ValueError(f"{label}: history lists {want.schema} more than once")
        _validate_want(label, want)
    _validate_bars_inside_definitions(label, entry)


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
    _validate_history(label, entry)


def validate_universe(entries: Sequence[TickUniverseEntry]) -> None:
    """Raise ``ValueError`` naming the field of the first invalid entry."""
    for entry in entries:
        _validate_entry(entry)
    products = [entry.product for entry in entries]
    duplicated = sorted({p for p in products if products.count(p) > 1})
    if duplicated:
        raise ValueError(f"tick universe: product listed twice: {duplicated}")


validate_universe(TICK_UNIVERSE)
