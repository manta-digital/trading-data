"""Databento metadata JSON → tick result types. Imports no ``databento`` symbol.

Every parser is strict: a missing key, a wrong type, or an unknown value
raises ``KeyError``/``TypeError``/``ValueError``, which the adapter maps to
``ProviderPermanentError`` (a malformed response, design TD 10).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd

from manta_trading.data.tick.constants import DatasetCondition
from manta_trading.data.tick.provider import (
    DatasetRange,
    DayCondition,
    SymbolInterval,
    SymbolResolution,
)

_KNOWN_CONDITIONS = frozenset(c.value for c in DatasetCondition)


def _as_mapping(raw: object, what: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"{what}: expected an object, got {type(raw).__name__}")
    return raw


def _as_list(raw: object, what: str) -> list[object]:
    if not isinstance(raw, list):
        raise TypeError(f"{what}: expected a list, got {type(raw).__name__}")
    return raw


def _as_str(raw: object, what: str) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"{what}: expected a string, got {type(raw).__name__}")
    return raw


def as_count(raw: object, what: str) -> int:
    """A non-negative integer (record count, byte size)."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise TypeError(f"{what}: expected a non-negative integer, got {raw!r}")
    return raw


def as_usd(raw: object) -> Decimal:
    """The SDK's float cost → ``Decimal`` via its shortest repr (exact cents)."""
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw < 0:
        raise TypeError(f"cost: expected a non-negative number, got {raw!r}")
    return Decimal(str(raw))


def _utc_datetime(raw: object, what: str) -> datetime:
    """ISO timestamp (nanosecond precision allowed) → aware UTC, µs grain."""
    stamp = pd.Timestamp(_as_str(raw, what))
    if stamp.tzinfo is None:
        raise ValueError(f"{what}: timestamp {raw!r} has no time zone")
    return stamp.tz_convert("UTC").floor("us").to_pydatetime()


def parse_dataset_range(raw: object) -> DatasetRange:
    body = _as_mapping(raw, "dataset range")
    return DatasetRange(
        start=_utc_datetime(body["start"], "dataset range start"),
        end=_utc_datetime(body["end"], "dataset range end"),
    )


def _condition(raw: object) -> DayCondition:
    body = _as_mapping(raw, "dataset condition")
    value = _as_str(body["condition"], "condition")
    if value not in _KNOWN_CONDITIONS:
        raise ValueError(f"unknown dataset condition {value!r}")
    modified = body["last_modified_date"]
    return DayCondition(
        day=date.fromisoformat(_as_str(body["date"], "condition date")),
        condition=DatasetCondition(value),
        last_modified=(
            None
            if modified is None
            else date.fromisoformat(_as_str(modified, "last_modified_date"))
        ),
    )


def parse_conditions(raw: object, start: date, end: date) -> tuple[DayCondition, ...]:
    """Exactly one condition per day of ``[start, end)``, in date order."""
    conditions = tuple(_condition(item) for item in _as_list(raw, "conditions"))
    expected = [start + timedelta(days=n) for n in range((end - start).days)]
    if [c.day for c in conditions] != expected:
        raise ValueError(
            f"dataset condition covers {len(conditions)} day(s); expected one "
            f"per day of [{start}, {end})"
        )
    return conditions


def _interval(raw: object) -> SymbolInterval:
    body = _as_mapping(raw, "symbology interval")
    return SymbolInterval(
        start_date=date.fromisoformat(_as_str(body["d0"], "d0")),
        end_date=date.fromisoformat(_as_str(body["d1"], "d1")),
        instrument_id=int(_as_str(body["s"], "s")),
    )


def _str_tuple(raw: object, what: str) -> tuple[str, ...]:
    return tuple(_as_str(item, what) for item in _as_list(raw, what))


def parse_resolution(raw: object) -> SymbolResolution:
    body = _as_mapping(raw, "symbology resolve")
    result = _as_mapping(body["result"], "symbology result")
    return SymbolResolution(
        mappings={
            symbol: tuple(_interval(item) for item in _as_list(entries, symbol))
            for symbol, entries in result.items()
        },
        partial=_str_tuple(body["partial"], "partial"),
        not_found=_str_tuple(body["not_found"], "not_found"),
    )
