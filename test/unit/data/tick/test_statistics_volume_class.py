"""``classify_volume``: traded volume V against cleared volume C (slice 303, D8)."""

from __future__ import annotations

from datetime import date

import pytest

from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_reads import (
    VOLUME_FINDINGS,
    VolumeClass,
    VolumeVerdict,
    classify_volume,
)


@pytest.mark.parametrize(
    ("cleared", "traded", "expected"),
    [
        (100, 100, VolumeClass.AGREE),
        (0, 0, VolumeClass.AGREE),
        (100, 60, VolumeClass.UNDER),
        (100, 1, VolumeClass.UNDER),
        (100, 101, VolumeClass.OVER),
        (0, 5, VolumeClass.OVER),
        (100, 0, VolumeClass.NO_TRADES),
    ],
)
def test_each_verdict(cleared: int, traded: int, expected: VolumeClass) -> None:
    assert classify_volume(cleared, traded) is expected


def test_only_over_and_no_trades_are_findings() -> None:
    assert VOLUME_FINDINGS == {VolumeClass.OVER, VolumeClass.NO_TRADES}
    assert VolumeClass.UNDER not in VOLUME_FINDINGS


def _verdict(cleared: int, traded: int) -> VolumeVerdict:
    return VolumeVerdict(
        date(2024, 12, 3),
        1,
        "ESZ4",
        SchemaFamily.TICK_TIER,
        cleared,
        traded,
        classify_volume(cleared, traded),
    )


def test_under_carries_its_ratio_and_a_zero_cleared_volume_has_none() -> None:
    assert _verdict(200, 150).ratio == 0.75
    assert _verdict(0, 0).ratio is None
