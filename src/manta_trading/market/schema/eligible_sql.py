"""Tape-eligible bar values as SQL expressions (slice 171).

For a bar with ``off_tape`` mask ``f``, let ``E`` be its unmarked values among
open/high/low/close, ``lo = min(E)`` and ``hi = max(E)``. Each value ``v``
becomes ``clamp(v, lo, hi)``. A bar with every value marked has no eligible
price and is excluded, price and volume (slice 171 design, "Eligible bar
values"). ``NULL`` (not evaluated) reads as clean.

Rendered from :class:`OffTape` only, so the bits have one definition. The
fragments contain column names, never user input. Slice 172 renders them
inside its rollup definitions via the ``alias`` parameter.
``data.quality.off_tape.eligible_bar`` is the Python mirror.
"""

from __future__ import annotations

from manta_trading.constants import OFF_TAPE_PRICE_BITS as PRICE_COLUMNS
from manta_trading.constants import OffTape


def _column(name: str, alias: str | None) -> str:
    return f"{alias}.{name}" if alias else name


def _mask(alias: str | None) -> str:
    return f"COALESCE({_column('off_tape', alias)}, 0)"


def _eligible_terms(alias: str | None) -> str:
    """``CASE`` per price: the value when its bit is clear, else ``NULL``."""
    mask = _mask(alias)
    return ", ".join(
        f"CASE WHEN {mask} & {int(bit)} = 0 THEN {_column(name, alias)} END"
        for name, bit in PRICE_COLUMNS.items()
    )


def eligible_low(alias: str | None = None) -> str:
    """Lowest unmarked price of the bar (``NULL`` when all are marked)."""
    return f"LEAST({_eligible_terms(alias)})"


def eligible_high(alias: str | None = None) -> str:
    """Highest unmarked price of the bar (``NULL`` when all are marked)."""
    return f"GREATEST({_eligible_terms(alias)})"


def eligible_value(column: str, alias: str | None = None) -> str:
    """``column`` clamped into the bar's eligible range."""
    if column not in PRICE_COLUMNS:
        raise ValueError(f"not a price column: {column!r}")
    value = _column(column, alias)
    return f"LEAST(GREATEST({value}, {eligible_low(alias)}), {eligible_high(alias)})"


def has_eligible_price(alias: str | None = None) -> str:
    """Predicate: the bar has at least one unmarked price."""
    return f"{_mask(alias)} <> {int(OffTape.ALL)}"
