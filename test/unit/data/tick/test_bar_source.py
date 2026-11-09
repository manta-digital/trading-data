"""``BAR_SOURCE``: every served bar size names its provider schema (slice 306 D2)."""

from __future__ import annotations

from manta_trading.data.tick.bar_constants import BAR_SOURCE
from manta_trading.data.tick.constants import TICK_BAR_GRANULARITIES, TickSchema


def test_every_served_size_is_built_from_ohlcv_1m() -> None:
    assert set(BAR_SOURCE) == set(TICK_BAR_GRANULARITIES)
    assert set(BAR_SOURCE.values()) == {TickSchema.OHLCV_1M}
