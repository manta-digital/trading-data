"""Metadata responses for the tick tests — the one place their shapes live.

These are built from the SDK's documented return types (``get_dataset_range``
→ ``{start, end}``; ``get_dataset_condition`` → a list of ``{date, condition,
last_modified_date}``; ``symbology.resolve`` → ``{result: {sym: [{d0, d1, s}]},
partial, not_found}``; counts and sizes are ints; cost is a float). Task 7.3
replaces them with a loader over the recorded fixtures.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from tick_support.fake_historical import FakeApi

from manta_trading.data.tick.constants import TickSchema

REQUEST_START = date(2025, 1, 6)
REQUEST_END = date(2025, 1, 11)
DATASET_END = "2025-09-26T00:00:00.000000000Z"

#: Per schema: record count, billable bytes, cost in USD.
FIGURES: dict[TickSchema, tuple[int, int, float]] = {
    TickSchema.TRADES: (2_000_000, 96_000_000, 1.25),
    TickSchema.TBBO: (2_000_000, 160_000_000, 2.5),
    TickSchema.MBP_1: (60_000_000, 4_800_000_000, 40.0),
    TickSchema.DEFINITION: (50, 26_000, 0.75),
}


def _by_schema(index: int) -> Any:
    return lambda kwargs: FIGURES[TickSchema(kwargs["schema"])][index]


def _conditions(kwargs: dict[str, Any]) -> list[dict[str, str | None]]:
    """One ``available`` entry per day of the inclusive range asked for."""
    start, end = kwargs["start_date"], kwargs["end_date"]
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    return [
        {
            "date": day.isoformat(),
            "condition": "available",
            "last_modified_date": (day + timedelta(days=1)).isoformat(),
        }
        for day in days
    ]


def metadata_api() -> FakeApi:
    return FakeApi(
        {
            "get_dataset_range": {
                "start": "2010-06-06T00:00:00.000000000Z",
                "end": DATASET_END,
            },
            "get_dataset_condition": _conditions,
            "get_record_count": _by_schema(0),
            "get_billable_size": _by_schema(1),
            "get_cost": _by_schema(2),
        }
    )


def symbology_api() -> FakeApi:
    return FakeApi(
        {
            "resolve": {
                "result": {
                    "ES.c.0": [{"d0": "2025-01-06", "d1": "2025-01-11", "s": "5002"}]
                },
                "symbols": ["ES.c.0"],
                "stype_in": "continuous",
                "stype_out": "instrument_id",
                "start_date": "2025-01-06",
                "end_date": "2025-01-11",
                "partial": [],
                "not_found": [],
                "message": "OK",
                "status": 0,
            }
        }
    )
