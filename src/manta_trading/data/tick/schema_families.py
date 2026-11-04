"""Schema families: which archived schemas can supersede one another (slice 300, D1).

Supersession is a rule *within* a family: a higher-ranked schema of the same
family replaces a lower one for the same shape and day. Schemas of different
families never compete (a definition unit and a tick-tier unit of the same day
are both kept). A new archived schema joins by adding one family here (302 adds
bars, 303 adds statistics); nothing else states the rank order.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from manta_trading.data.tick.bar_constants import BAR_LEDGER_TABLE
from manta_trading.data.tick.constants import (
    TICK_LEDGER_TABLE,
    TICK_TIERS,
    TickSchema,
)
from manta_trading.data.tick.statistics_constants import STATISTICS_LEDGER_TABLE


class SchemaFamily(StrEnum):
    TICK_TIER = "tick_tier"
    DEFINITION = "definition"
    BAR_1M = "bar_1m"
    STATISTICS = "statistics"


#: Family -> schemas, lowest rank first. The tick-tier order is ``TICK_TIERS``,
#: the one place it is stated.
FAMILY_SCHEMAS: Final[Mapping[SchemaFamily, tuple[TickSchema, ...]]] = {
    SchemaFamily.TICK_TIER: TICK_TIERS,
    SchemaFamily.DEFINITION: (TickSchema.DEFINITION,),
    SchemaFamily.BAR_1M: (TickSchema.OHLCV_1M,),
    SchemaFamily.STATISTICS: (TickSchema.STATISTICS,),
}

#: Family -> the ledger table its ingest writes. ``DEFINITION`` is absent on
#: purpose: the acquisition pass projects definitions, ingest never does.
FAMILY_LEDGER: Final[Mapping[SchemaFamily, str]] = {
    SchemaFamily.TICK_TIER: TICK_LEDGER_TABLE,
    SchemaFamily.BAR_1M: BAR_LEDGER_TABLE,
    SchemaFamily.STATISTICS: STATISTICS_LEDGER_TABLE,
}


#: The families whose ledger carries a traded ``volume`` (the statistics ledger
#: does not), in the order the volume rule prefers them (303 D10): the tick tier
#: first, so every answer decided on ticks is what it was before bars existed.
VOLUME_FAMILIES: Final[tuple[SchemaFamily, ...]] = (
    SchemaFamily.TICK_TIER,
    SchemaFamily.BAR_1M,
)


def schema_family_map(
    table: Mapping[SchemaFamily, tuple[TickSchema, ...]],
) -> dict[TickSchema, SchemaFamily]:
    """Invert ``table``; a schema listed in two families is a defect, not a choice."""
    inverted: dict[TickSchema, SchemaFamily] = {}
    for family, schemas in table.items():
        for schema in schemas:
            if schema in inverted:
                raise ValueError(
                    f"schema {schema} is in two families: {inverted[schema]} and"
                    f" {family}"
                )
            inverted[schema] = family
    return inverted


SCHEMA_FAMILY: Final[Mapping[TickSchema, SchemaFamily]] = schema_family_map(
    FAMILY_SCHEMAS
)


def _case(column: str, values: Mapping[TickSchema, object]) -> str:
    """``CASE`` over ``column`` (cast to text so a bind placeholder is typed)."""
    arms = " ".join(
        f"WHEN '{schema.value}' THEN {value}" for schema, value in values.items()
    )
    return f"CASE ({column})::text {arms} END"


def supersedes_sql(newer: str, older: str) -> str:
    """SQL predicate: ``newer`` and ``older`` share a family and ``newer`` ranks higher.

    ``newer`` and ``older`` are interpolated verbatim: column references or bind
    placeholders (``%(schema)s``) written in code, never input. The predicate
    binds no values of its own. A schema in no family yields NULL, never true.
    Reads ``FAMILY_SCHEMAS`` at call time.
    """
    family = {
        schema: f"'{fam.value}'"
        for fam, schemas in FAMILY_SCHEMAS.items()
        for schema in schemas
    }
    rank = {
        schema: position
        for schemas in FAMILY_SCHEMAS.values()
        for position, schema in enumerate(schemas)
    }
    return (
        f"({_case(newer, family)} = {_case(older, family)}"
        f" AND {_case(newer, rank)} > {_case(older, rank)})"
    )
