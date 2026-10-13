"""Path and query parsing shared by the futures routes (slice 231, TD1).

Products resolve from ``TICK_UNIVERSE`` (never a literal), so a product added
there (232's GC) is served with no route change. Rules go through 229's
``RollRule.parse``; its message is the ``422`` text.
"""

from __future__ import annotations

from fastapi import HTTPException
from starlette import status as http_status

from manta_trading.api_server.admission import not_found
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE, RollRule
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

#: The ``rule`` query parameter's description, shared by every route taking it.
RULE_DESCRIPTION = (
    "Roll rule, {method}.{rank}[+{offset}s] (e.g. v.0, c.0, c.0+2s); "
    f"default {DEFAULT_ROLL_RULE}."
)


def product_entry(product: str) -> TickUniverseEntry:
    """The universe entry for ``product``, or ``404``."""
    for entry in TICK_UNIVERSE:
        if entry.product == product:
            return entry
    raise not_found("Product", product)


def parse_rule(rule: str | None) -> RollRule:
    """``rule`` parsed by 229's grammar, the default when omitted, or ``422``."""
    if rule is None:
        return DEFAULT_ROLL_RULE
    try:
        return RollRule.parse(rule)
    except ValueError as exc:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from exc
