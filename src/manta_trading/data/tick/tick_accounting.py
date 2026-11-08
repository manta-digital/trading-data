"""The futures lines in ``mt data accounting`` and ``/api/v1/status``.

Slice 230 TD7, FR12; slice 305 D6. One :class:`TickAccountingLine` per product
and family, converted from ``futures_completeness``'s reports. This module reads
nothing: ``family_report.futures_completeness`` is the only reader, so the
buckets here are ``status``'s own (the 230 rule, now for every family). Records
are the ledger sum over the family's current units. No CLI import: 231's API
reuses it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby

from manta_trading.data.tick.cross_tier_result import CrossTierResult
from manta_trading.data.tick.family_report import FamilyStatus
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_status import TickSessionStatus


@dataclass(frozen=True)
class TickAccountingLine:
    product: str
    family: str
    #: The schema the product wants the family as; None for an unwanted tick tier.
    schema: str | None
    sessions_in_scope: int
    sessions_complete: int
    buckets: dict[str, int]
    records: int
    caught_up: bool | None
    source_edge: datetime | None
    load_complete_only: bool
    cross_tier: tuple[CrossTierResult, ...]


def _line(product: str, report: FamilyStatus) -> TickAccountingLine:
    return TickAccountingLine(
        product=product,
        family=report.family,
        schema=report.schema,
        sessions_in_scope=sum(report.buckets.values()),
        sessions_complete=report.buckets[TickSessionStatus.COMPLETE.value],
        buckets=report.buckets,
        records=report.records,
        caught_up=report.caught_up,
        source_edge=report.source_edge,
        load_complete_only=report.load_complete_only,
        cross_tier=report.cross_tier,
    )


def accounting_lines(
    reports_by_product: Mapping[str, Sequence[FamilyStatus]],
) -> list[TickAccountingLine]:
    """A pure conversion: one line per product and family, in report order."""
    return [
        _line(product, report)
        for product, reports in reports_by_product.items()
        for report in reports
    ]


def lines_by_product(
    lines: Sequence[TickAccountingLine],
) -> list[tuple[str, list[TickAccountingLine]]]:
    """Lines grouped by product, in order (a product's lines are adjacent)."""
    return [(p, list(group)) for p, group in groupby(lines, key=lambda x: x.product)]


def tier_line(product: str, lines: Sequence[TickAccountingLine]) -> TickAccountingLine:
    """The product's tick-tier line; ``futures_completeness`` emits exactly one."""
    tiers = [x for x in lines if x.family == SchemaFamily.TICK_TIER.value]
    if len(tiers) != 1:
        raise ValueError(
            f"{product}: expected one tick-tier accounting line, got {len(tiers)}"
        )
    return tiers[0]
