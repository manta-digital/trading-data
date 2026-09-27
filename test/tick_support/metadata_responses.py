"""Recorded Databento metadata responses for the tick tests (slice 220, TD 11).

Loads ``test/fixtures/databento/metadata/*.json``, written by
``scripts/record_databento_fixtures.py`` from the live free endpoints on
2026-09-27: ``ES.c.0`` (``continuous``), ``GLBX.MDP3``, ``[2025-01-06,
2025-01-11)``, every schema in ``ESTIMATE_SCHEMAS``. Every metadata answer a
test sees comes from these files; a test that needs another day range or
another condition derives it from a recorded entry (``condition_entries``).
"""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from tick_support.fake_historical import FakeApi

from manta_trading.data.tick.constants import ESTIMATE_SCHEMAS, TickSchema

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "databento" / "metadata"
)


@cache
def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def recorded(method: str, schema: TickSchema | None = None) -> Any:
    """A deep copy of the recorded response, safe for a test to mutate."""
    name = f"{method}.{schema.value}" if schema else method
    return copy.deepcopy(_load(name)["response"])


_CONDITIONS_REQUEST = _load("get_dataset_condition")["request"]
REQUEST_START = date.fromisoformat(_CONDITIONS_REQUEST["start_date"])
#: Exclusive; the recorded call sent the inclusive ``end_date`` one day earlier.
REQUEST_END = date.fromisoformat(_CONDITIONS_REQUEST["end_date"]) + timedelta(days=1)
DATASET_END: str = recorded("get_dataset_range")["end"]

#: Per schema, as the adapter returns them: records, billable bytes, USD.
FIGURES: dict[TickSchema, tuple[int, int, Decimal]] = {
    schema: (
        recorded("get_record_count", schema),
        recorded("get_billable_size", schema),
        Decimal(str(recorded("get_cost", schema))),
    )
    for schema in ESTIMATE_SCHEMAS
}


def bundle(schema: TickSchema) -> Decimal:
    """A stored tier's cost plus the ``definition`` cost, from the recordings."""
    return FIGURES[schema][2] + FIGURES[TickSchema.DEFINITION][2]


def condition_entries(start: date, end_inclusive: date) -> list[dict[str, Any]]:
    """The recorded condition entries re-dated to cover ``[start, end_inclusive]``.

    Shape and values are the recorded ones; only ``date`` changes.
    """
    template = recorded("get_dataset_condition")[0]
    days = (end_inclusive - start).days + 1
    return [
        {**template, "date": (start + timedelta(days=n)).isoformat()}
        for n in range(days)
    ]


def _conditions(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    """The recording itself for the recorded range; re-dated entries otherwise."""
    start, end_inclusive = kwargs["start_date"], kwargs["end_date"]
    if (start.isoformat(), end_inclusive.isoformat()) == (
        _CONDITIONS_REQUEST["start_date"],
        _CONDITIONS_REQUEST["end_date"],
    ):
        return recorded("get_dataset_condition")
    return condition_entries(start, end_inclusive)


def _by_schema(method: str) -> Any:
    return lambda kwargs: recorded(method, TickSchema(kwargs["schema"]))


def metadata_api() -> FakeApi:
    return FakeApi(
        {
            "get_dataset_range": lambda _: recorded("get_dataset_range"),
            "get_dataset_condition": _conditions,
            "get_record_count": _by_schema("get_record_count"),
            "get_billable_size": _by_schema("get_billable_size"),
            "get_cost": _by_schema("get_cost"),
        }
    )


def symbology_api() -> FakeApi:
    return FakeApi({"resolve": lambda _: recorded("resolve")})
