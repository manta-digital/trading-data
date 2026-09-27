"""Tick cost-and-size preflight core (slice 220, design *Data Flow*).

``build_estimate`` accepts an ``ITickMetadataProvider`` only — a protocol with
no paid method — so the preflight cannot spend by construction. It imports
nothing from ``data/tick/databento/``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from manta_trading.data.tick.constants import (
    COMPANION_SCHEMAS,
    ESTIMATE_SCHEMAS,
    STORED_TIERS,
    TICK_SPEND_CEILING_ENV,
    DatasetCondition,
    TickSchema,
)
from manta_trading.data.tick.provider import (
    DatasetRange,
    ITickMetadataProvider,
    TickRequest,
)


class CeilingVerdict(StrEnum):
    """The verdict column: a stored tier's bundle against the ceiling, or why
    a row has no bundle verdict (design Technical Decision 5)."""

    WITHIN = "within"
    OVER = "over"
    NO_CEILING = f"no ceiling configured ({TICK_SPEND_CEILING_ENV} unset)"
    BOUGHT_WITH_EACH_TIER = "bought with each tier"
    NOT_PURCHASABLE = "not purchasable in this initiative"


class EstimateRefusedError(Exception):
    """The request cannot be estimated as asked (for example, end past the edge)."""


@dataclass(frozen=True)
class SchemaEstimate:
    """One row: a schema's figures and, for a stored tier, its bundle verdict."""

    schema: TickSchema
    record_count: int
    billable_bytes: int
    cost_usd: Decimal
    #: Tier cost plus the companion ``definition`` cost; stored tiers only.
    bundle_cost_usd: Decimal | None
    verdict: CeilingVerdict


@dataclass(frozen=True)
class EstimateReport:
    request: TickRequest
    available: DatasetRange
    #: Days of the request per provider condition; each day counted once.
    conditions: dict[DatasetCondition, int]
    rows: tuple[SchemaEstimate, ...]
    ceiling_usd: Decimal | None

    def to_dict(self) -> dict[str, object]:
        """The ``--json`` payload. Money is a decimal string, never a float."""
        return {
            "dataset": self.request.dataset,
            "symbols": list(self.request.symbols),
            "stype_in": self.request.stype_in.value,
            "start": self.request.start.isoformat(),
            "end_exclusive": self.request.end.isoformat(),
            "available": {
                "start": self.available.start.isoformat(),
                "end": self.available.end.isoformat(),
            },
            "conditions": {c.value: n for c, n in self.conditions.items()},
            "ceiling_usd": _money(self.ceiling_usd),
            "schemas": [
                {
                    "schema": row.schema.value,
                    "record_count": row.record_count,
                    "billable_bytes": row.billable_bytes,
                    "cost_usd": _money(row.cost_usd),
                    "bundle_cost_usd": _money(row.bundle_cost_usd),
                    "ceiling_verdict": row.verdict.value,
                }
                for row in self.rows
            ],
        }


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _bundle_verdict(bundle: Decimal, ceiling: Decimal | None) -> CeilingVerdict:
    if ceiling is None:
        return CeilingVerdict.NO_CEILING
    return CeilingVerdict.WITHIN if bundle <= ceiling else CeilingVerdict.OVER


def _row(
    schema: TickSchema,
    figures: dict[TickSchema, tuple[int, int, Decimal]],
    ceiling: Decimal | None,
) -> SchemaEstimate:
    records, size, cost = figures[schema]
    bundle: Decimal | None = None
    if schema in STORED_TIERS:
        bundle = cost + sum(
            (figures[c][2] for c in COMPANION_SCHEMAS), start=Decimal(0)
        )
        verdict = _bundle_verdict(bundle, ceiling)
    elif schema in COMPANION_SCHEMAS:
        verdict = CeilingVerdict.BOUGHT_WITH_EACH_TIER
    else:
        verdict = CeilingVerdict.NOT_PURCHASABLE
    return SchemaEstimate(schema, records, size, cost, bundle, verdict)


def build_estimate(
    metadata: ITickMetadataProvider,
    request: TickRequest,
    ceiling: Decimal | None,
) -> EstimateReport:
    """Price ``request`` at every schema in ``ESTIMATE_SCHEMAS``; free calls only.

    Raises ``EstimateRefusedError`` naming the edge when ``request.end`` is
    past the dataset's available end.
    """
    available = metadata.dataset_range(request.dataset)
    if request.end_utc > available.end:
        raise EstimateRefusedError(
            f"requested end {request.end} (exclusive) is past the dataset's "
            f"available end {available.end.isoformat()}"
        )
    days = metadata.dataset_condition(request.dataset, request.start, request.end)
    tally = Counter(day.condition for day in days)
    figures: dict[TickSchema, tuple[int, int, Decimal]] = {}
    for schema in ESTIMATE_SCHEMAS:
        at_schema = request.with_schema(schema)
        figures[schema] = (
            metadata.record_count(at_schema),
            metadata.billable_size(at_schema),
            metadata.cost(at_schema),
        )
    return EstimateReport(
        request=request,
        available=available,
        conditions={c: tally[c] for c in DatasetCondition},
        rows=tuple(_row(schema, figures, ceiling) for schema in ESTIMATE_SCHEMAS),
        ceiling_usd=ceiling,
    )
