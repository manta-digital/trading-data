"""Tick constants: provider spellings, tier sets (slice 220, TD 4) and the
storage vocabulary (slice 222, TD 4, 8, 9)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from manta_trading.data.kalshi.constants import SYNC_ADVISORY_LOCK_KEY
from manta_trading.data.tick.constants import (
    ARCHIVED_SCHEMAS,
    COMPANION_SCHEMAS,
    ESTIMATE_SCHEMAS,
    STORED_TIERS,
    TICK_ACQUISITION_LOCK_KEY,
    TICK_ARCHIVE_DIR_ENV,
    TICK_DB_CONNECT_TIMEOUT_SECONDS,
    TICK_ENV_PREFIX,
    TICK_SPEND_30D_CEILING_ENV,
    TICK_SPEND_CEILING_ENV,
    TICK_TIERS,
    TICK_TRADE_CHUNK_INTERVAL,
    UNIT_STATES_WITH_FILE,
    DeliveryMode,
    SType,
    TickSchema,
    UnitState,
    calendar_for_product,
)
from manta_trading.market.schema.seed_cme_calendar import CME_EQUITY_CALENDAR_ID


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


def test_calendar_for_known_product() -> None:
    assert calendar_for_product("ES") == CME_EQUITY_CALENDAR_ID


def test_calendar_for_unknown_product_names_known_ones() -> None:
    with pytest.raises(KeyError, match="'GC'.*known: ES"):
        calendar_for_product("GC")


def test_unit_state_values_in_lifecycle_order() -> None:
    assert [s.value for s in UnitState] == [
        "requested",
        "submitted",
        "delivered",
        "downloaded",
        "verified",
        "ingested",
    ]


def test_unit_states_with_file_come_after_delivery() -> None:
    order = list(UnitState)
    assert UNIT_STATES_WITH_FILE == {
        UnitState.DOWNLOADED,
        UnitState.VERIFIED,
        UnitState.INGESTED,
    }
    delivered = order.index(UnitState.DELIVERED)
    assert all(order.index(s) > delivered for s in UNIT_STATES_WITH_FILE)


def test_archived_schemas_are_stored_tiers_and_definition() -> None:
    assert ARCHIVED_SCHEMAS == {
        TickSchema.TRADES,
        TickSchema.TBBO,
        TickSchema.DEFINITION,
    }
    assert TickSchema.MBP_1 not in ARCHIVED_SCHEMAS


def test_tick_trade_chunk_interval_is_seven_days() -> None:
    assert TICK_TRADE_CHUNK_INTERVAL == timedelta(days=7)


def test_acquisition_lock_key_is_its_own() -> None:
    assert TICK_ACQUISITION_LOCK_KEY == 220_000_001
    assert TICK_ACQUISITION_LOCK_KEY != SYNC_ADVISORY_LOCK_KEY


def test_run_context_constants() -> None:
    assert TICK_DB_CONNECT_TIMEOUT_SECONDS == 10
    assert TICK_SPEND_30D_CEILING_ENV == "MT_TICK_SPEND_30D_CEILING_USD"
    assert TICK_ARCHIVE_DIR_ENV == "MT_TICK_ARCHIVE_DIR"
    assert TICK_ENV_PREFIX == "MT_TICK_"


def test_every_tick_env_name_carries_the_prefix() -> None:
    for name in (
        TICK_SPEND_CEILING_ENV,
        TICK_SPEND_30D_CEILING_ENV,
        TICK_ARCHIVE_DIR_ENV,
    ):
        assert name.startswith(TICK_ENV_PREFIX)
