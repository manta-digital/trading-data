"""The availability span covers every want, history wants included (slice 307)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from manta_trading.data.tick.availability import availability_span
from manta_trading.data.tick.constants import CME_DATASET
from manta_trading.data.tick.provider import DatasetRange
from manta_trading.data.tick.universe import TICK_UNIVERSE

EDGE = DatasetRange(
    datetime(2010, 6, 6, tzinfo=UTC), datetime(2026, 11, 14, 20, tzinfo=UTC)
)


def test_the_span_reaches_back_to_the_oldest_history_want() -> None:
    span = availability_span(CME_DATASET, TICK_UNIVERSE, [], [], (None, None), EDGE)
    oldest = min(want.start for e in TICK_UNIVERSE for want in e.history)
    latest = max(want.end for e in TICK_UNIVERSE for want in e.history if want.end)
    assert span is not None
    assert oldest == date(2010, 6, 8)
    assert span.start == oldest  # the tier alone would start at 2024-11-02
    assert span.end >= latest
