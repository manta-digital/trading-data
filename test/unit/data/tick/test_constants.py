"""Tick constants: provider spellings and the tier sets (slice 220, TD 4)."""

from __future__ import annotations

from manta_trading.data.tick.constants import (
    COMPANION_SCHEMAS,
    ESTIMATE_SCHEMAS,
    STORED_TIERS,
    TICK_TIERS,
    DeliveryMode,
    SType,
    TickSchema,
)


def test_schema_values_are_provider_spellings() -> None:
    assert [s.value for s in TickSchema] == ["trades", "tbbo", "mbp-1", "definition"]


def test_stype_values_are_provider_spellings() -> None:
    assert [s.value for s in SType] == [
        "raw_symbol",
        "instrument_id",
        "parent",
        "continuous",
    ]


def test_delivery_mode_values() -> None:
    assert [m.value for m in DeliveryMode] == ["batch_job", "direct_range"]


def test_stored_tiers_exclude_mbp_1() -> None:
    assert STORED_TIERS == {TickSchema.TRADES, TickSchema.TBBO}
    assert TickSchema.MBP_1 not in STORED_TIERS


def test_stored_tiers_are_tiers() -> None:
    assert STORED_TIERS <= set(TICK_TIERS)


def test_companion_is_definition_only() -> None:
    assert COMPANION_SCHEMAS == {TickSchema.DEFINITION}
    assert not COMPANION_SCHEMAS & set(TICK_TIERS)


def test_estimate_schemas_are_every_tier_then_definition() -> None:
    assert ESTIMATE_SCHEMAS[-1] is TickSchema.DEFINITION
    assert set(TICK_TIERS) <= set(ESTIMATE_SCHEMAS)
    assert len(ESTIMATE_SCHEMAS) == len(set(ESTIMATE_SCHEMAS))
