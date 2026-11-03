"""The ingest projector registry (slice 300, D2)."""

from __future__ import annotations

import pytest

from manta_trading.data.tick.constants import STORED_TIERS, TickSchema
from manta_trading.data.tick.ohlcv_1m_projector import OHLCV_1M_PROJECTOR
from manta_trading.data.tick.projectors import (
    INGEST_PROJECTORS,
    NoProjectorError,
    projector_for,
)
from manta_trading.data.tick.tick_trade_projector import TICK_TRADE


def test_registry_keys_are_the_stored_tiers_and_the_bar_schema() -> None:
    assert set(INGEST_PROJECTORS) == STORED_TIERS | {TickSchema.OHLCV_1M}


def test_definition_is_not_registered() -> None:
    assert TickSchema.DEFINITION not in INGEST_PROJECTORS


def test_both_tiers_share_the_tick_trade_projector() -> None:
    assert INGEST_PROJECTORS[TickSchema.TRADES] is TICK_TRADE
    assert INGEST_PROJECTORS[TickSchema.TBBO] is TICK_TRADE


def test_ohlcv_1m_has_its_own_projector() -> None:
    assert INGEST_PROJECTORS[TickSchema.OHLCV_1M] is OHLCV_1M_PROJECTOR
    assert OHLCV_1M_PROJECTOR is not TICK_TRADE


def test_an_unregistered_schema_fails_naming_it() -> None:
    with pytest.raises(NoProjectorError, match="schema definition has no ingest"):
        projector_for(TickSchema.DEFINITION)
