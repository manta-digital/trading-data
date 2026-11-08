"""The futures section of ``mt data accounting`` (slice 230 TD7, slice 305 D6).

Text: lines grouped by product, one per family, then the cross-tier checks that
do not run. JSON: ``tick.products`` keeps one entry per product whose top-level
fields are the tick tier's (231's consumers read those), plus a ``families``
list carrying every family, the tick tier included.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from itertools import groupby
from typing import Any, Final

from manta_trading.cli.commands.tick_status_render import (
    cross_tier_lines,
    not_active_lines,
)
from manta_trading.data.tick.cross_tier_checks import INACTIVE_CROSS_TIER_CHECKS
from manta_trading.data.tick.family_report import LOAD_COMPLETE_ONLY
from manta_trading.data.tick.line_state import (
    TickLineState,
    TickUnavailable,
    unavailable_text,
)
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_accounting import TickAccountingLine
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_build import jsonable

TICK_SECTION_TITLE: Final = "futures universe:"
#: A product's own (top-level) JSON fields: the tick tier's, as in 231.
_TIER_FIELDS: Final = (
    "product",
    "sessions_in_scope",
    "sessions_complete",
    "buckets",
    "records",
)

TickResult = list[TickAccountingLine] | TickUnavailable


def tick_payload(tick: TickResult) -> dict[str, Any]:
    if isinstance(tick, TickUnavailable):
        return {"state": tick.state.value, "detail": tick.detail, "products": []}
    return {
        "state": TickLineState.OK.value,
        "detail": None,
        "products": [_product_payload(lines) for _, lines in _by_product(tick)],
    }


def _by_product(
    lines: Sequence[TickAccountingLine],
) -> list[tuple[str, list[TickAccountingLine]]]:
    """Lines grouped by product, in order (a product's lines are adjacent)."""
    return [(p, list(group)) for p, group in groupby(lines, key=lambda x: x.product)]


def _product_payload(lines: list[TickAccountingLine]) -> dict[str, Any]:
    (tier,) = [x for x in lines if x.family == SchemaFamily.TICK_TIER.value]
    body = asdict(tier)
    return {k: body[k] for k in _TIER_FIELDS} | {
        "families": [jsonable(asdict(x)) for x in lines]
    }


def _line_text(line: TickAccountingLine) -> str:
    name = line.schema or "tick tier (not wanted)"
    if line.sessions_in_scope == 0:
        return f"    {name}  no sessions in scope"
    share = 100 * line.sessions_complete / line.sessions_in_scope
    rest = "; ".join(
        f"{count} {status.replace('_', ' ')}"
        for status, count in line.buckets.items()
        if count and status != TickSessionStatus.COMPLETE.value
    )
    caught_up = {None: "n/a", True: "yes", False: "no"}[line.caught_up]
    edge = "" if line.source_edge is None else f" to {line.source_edge:%Y-%m-%d}"
    return (
        f"    {name}  {line.sessions_complete}/{line.sessions_in_scope}"
        f" sessions complete ({share:.1f}%)"
        + (f"; {rest}" if rest else "")
        + f"  · {line.records:,} records  · caught up {caught_up}{edge}"
        + (f"  · {LOAD_COMPLETE_ONLY}" if line.load_complete_only else "")
    )


def tick_text(tick: TickResult) -> str:
    if isinstance(tick, TickUnavailable):
        return f"{TICK_SECTION_TITLE}  {unavailable_text(tick)}"
    out = [TICK_SECTION_TITLE]
    for product, lines in _by_product(tick):
        out.append(f"  {product}")
        for line in lines:
            out.append(_line_text(line))
            out.extend(
                f"    {text.strip()}" for text in cross_tier_lines(line.cross_tier)
            )
    return "\n".join([*out, *not_active_lines(INACTIVE_CROSS_TIER_CHECKS)])
