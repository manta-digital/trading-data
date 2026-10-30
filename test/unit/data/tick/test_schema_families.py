"""Schema families and the family-scoped supersession predicate (slice 300, D1)."""

from __future__ import annotations

from enum import StrEnum

import pytest

from manta_trading.data.tick import schema_families
from manta_trading.data.tick.constants import TICK_TIERS, TickSchema
from manta_trading.data.tick.schema_families import (
    FAMILY_SCHEMAS,
    SCHEMA_FAMILY,
    SchemaFamily,
    schema_family_map,
    supersedes_sql,
)


def test_every_schema_is_in_exactly_one_family() -> None:
    listed = [schema for schemas in FAMILY_SCHEMAS.values() for schema in schemas]
    assert sorted(listed) == sorted(TickSchema)


def test_schema_family_agrees_with_the_table() -> None:
    for family, schemas in FAMILY_SCHEMAS.items():
        assert all(SCHEMA_FAMILY[schema] is family for schema in schemas)
    assert FAMILY_SCHEMAS[SchemaFamily.TICK_TIER] is TICK_TIERS


def test_a_schema_in_two_families_fails_explicitly() -> None:
    table = {
        SchemaFamily.TICK_TIER: (TickSchema.TRADES,),
        SchemaFamily.DEFINITION: (TickSchema.TRADES,),
    }
    with pytest.raises(ValueError, match="two families"):
        schema_family_map(table)


def test_supersedes_sql_compares_family_and_rank_and_binds_nothing() -> None:
    rendered = supersedes_sql("p.schema", "r.schema")
    assert "CASE (p.schema)::text" in rendered
    assert "CASE (r.schema)::text" in rendered
    assert "'tick_tier'" in rendered
    assert " = " in rendered and " > " in rendered
    assert "%" not in rendered


def test_supersedes_sql_keeps_a_bind_placeholder_verbatim() -> None:
    assert "(%(schema)s)::text" in supersedes_sql("%(schema)s", "p.schema")


def test_a_new_family_appears_in_the_rendered_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Fake(StrEnum):
        BARS = "fake-bars"

    extended = {
        **FAMILY_SCHEMAS,
        SchemaFamily.DEFINITION: (TickSchema.DEFINITION, _Fake.BARS),  # type: ignore[dict-item]
    }
    monkeypatch.setattr(schema_families, "FAMILY_SCHEMAS", extended)
    rendered = supersedes_sql("p.schema", "r.schema")
    assert "WHEN 'fake-bars' THEN 'definition'" in rendered
    assert "WHEN 'fake-bars' THEN 1" in rendered
