"""The availability span covers every want, history wants included (slice 307).

Production wants no history since slice 309, so the span is checked on
``WITH_HISTORY``: production's entries with the wants they carried until then.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from tick_support.universe import HISTORY_START, WITH_HISTORY

from manta_trading.data.tick.availability import availability_span
from manta_trading.data.tick.constants import CME_DATASET
from manta_trading.data.tick.provider import DatasetRange

EDGE = DatasetRange(
    datetime(2010, 6, 6, tzinfo=UTC), datetime(2026, 11, 14, 20, tzinfo=UTC)
)


def test_the_span_reaches_back_to_the_oldest_history_want() -> None:
    span = availability_span(CME_DATASET, WITH_HISTORY, [], [], (None, None), EDGE)
    oldest = min(want.start for e in WITH_HISTORY for want in e.history)
    latest = max(want.end for e in WITH_HISTORY for want in e.history if want.end)
    assert span is not None
    assert oldest == HISTORY_START == date(2010, 6, 8)
    assert span.start == oldest  # the tier alone would start at 2024-11-02
    assert span.end >= latest
