"""Path and query parsing shared by the futures routes (slice 231, TD1).

Products resolve from ``TICK_UNIVERSE`` (never a literal), so a product added
there (232's GC) is served with no route change. Rules go through 229's
``RollRule.parse``; its message is the ``422`` text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from fastapi import HTTPException, Query
from starlette import status as http_status

from manta_trading.api_server.admission import not_found, range_reversed
from manta_trading.api_server.routes.windows import instant_utc
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE, RollRule
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

#: The ``rule`` query parameter's description, shared by every route taking it.
RULE_DESCRIPTION = (
    "Roll rule, {method}.{rank}[+{offset}s] (e.g. v.0, n.0, c.0, c.0+2s); "
    f"default {DEFAULT_ROLL_RULE}."
)

START_DESCRIPTION = (
    "First session date. Omitted: the first session [from, until) reaches,"
    " else the first held session."
)
END_DESCRIPTION = (
    "End session date, EXCLUSIVE (unlike /api/v1/bars). Omitted: the day after"
    " the last session [from, until) reaches, else after the last held session."
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


@dataclass(frozen=True)
class InstantBounds:
    """``[from, until)`` as aware UTC instants (echoed) and epoch ns (read)."""

    lower: datetime | None
    upper: datetime | None

    def ns(self) -> tuple[int | None, int | None]:
        return (
            None if self.lower is None else utc_ns(self.lower),
            None if self.upper is None else utc_ns(self.upper),
        )


def instant_bounds(from_: datetime | None, until: datetime | None) -> InstantBounds:
    """``[from, until)`` (TD3), normalized once; naive reads as UTC. ``422``
    unless ``from`` is before ``until``."""
    lower = None if from_ is None else instant_utc(from_)
    upper = None if until is None else instant_utc(until)
    if lower is not None and upper is not None and lower >= upper:
        raise range_reversed(
            f"from ({lower.isoformat()}) must be before until "
            f"({upper.isoformat()}); the bounds are half-open, [from, until)"
        )
    return InstantBounds(lower, upper)


RuleParam = Annotated[str | None, Query(description=RULE_DESCRIPTION)]
StartParam = Annotated[date | None, Query(description=START_DESCRIPTION)]
EndParam = Annotated[date | None, Query(description=END_DESCRIPTION)]
FromParam = Annotated[
    datetime | None,
    Query(
        alias="from",
        description=(
            "First instant, inclusive (UTC; naive is UTC). Ticks are clipped"
            " to it; tick bars are selected by open_time, time bars by their"
            " start, each bar kept whole."
        ),
    ),
]
UntilParam = Annotated[
    datetime | None,
    Query(
        description=(
            "Instant bound, EXCLUSIVE (UTC; naive is UTC). Ticks are clipped"
            " to it; tick bars are selected by open_time, time bars by their"
            " start, each bar kept whole."
        )
    ),
]
TicksPerBarParam = Annotated[
    int,
    Query(
        ge=1,
        description=(
            "Trades per bar, any positive integer. The count restarts at each"
            " session open; a session's last bar may be partial."
        ),
    ),
]
