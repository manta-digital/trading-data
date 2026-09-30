"""Tick types ↔ Databento wire shapes. Imports no ``databento`` symbol.

``query`` turns a ``TickRequest`` into the SDK's keywords; the ``parse_*``
functions turn the SDK's JSON answers into the tick result types.

Every parser is strict: a missing key, a wrong type, or an unknown value
raises ``KeyError``/``TypeError``/``ValueError``, which the adapter maps to
``ProviderPermanentError`` (a malformed response, design TD 10).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import pandas as pd

from manta_trading.data.tick.constants import (
    CONDITION_ABSENT_WEEKDAY,
    BatchJobState,
    DatasetCondition,
    SType,
    TickSchema,
)
from manta_trading.data.tick.databento._download import BatchFile
from manta_trading.data.tick.provider import (
    BatchJob,
    DatasetRange,
    DayCondition,
    SymbolInterval,
    SymbolResolution,
    TickRequest,
)


class Query(TypedDict):
    """The keywords the SDK's metadata and submit calls share."""

    dataset: str
    symbols: list[str]
    schema: str
    stype_in: str
    start: datetime
    end: datetime


def query(request: TickRequest) -> Query:
    """The request's parameters as the SDK names them.

    Bounds go out as UTC-midnight datetimes, never bare dates: the API
    forward-fills a bare date by its resolution (the SDK's quickstart shows a
    job submitted with ``end="2020-12-29"`` stored as ``2020-12-30 00:00``),
    which would make the exclusive end inclusive (design TD 8).
    """
    return {
        "dataset": request.dataset,
        "symbols": list(request.symbols),
        "schema": request.schema.value,
        "stype_in": request.stype_in.value,
        "start": request.start_utc,
        "end": request.end_utc,
    }


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
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"{what}: expected an integer, got {raw!r}")
    if raw < 0:
        raise ValueError(f"{what}: expected a non-negative integer, got {raw}")
    return raw


def as_usd(raw: object) -> Decimal:
    """The SDK's float cost → ``Decimal`` via its shortest repr (exact cents)."""
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise TypeError(f"cost: expected a number, got {raw!r}")
    if raw < 0:
        raise ValueError(f"cost: expected a non-negative number, got {raw}")
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
    """One condition per day of ``[start, end)``, in date order.

    The provider omits Saturdays (``CONDITION_ABSENT_WEEKDAY``), so a Saturday
    may be absent; any other absent, repeated, unordered or out-of-range day is
    an unusable answer.
    """
    conditions = tuple(_condition(item) for item in _as_list(raw, "conditions"))
    days = [c.day for c in conditions]
    every = [start + timedelta(days=n) for n in range((end - start).days)]
    required = {d for d in every if d.weekday() != CONDITION_ABSENT_WEEKDAY}
    if days != sorted(set(days)) or not required <= set(days) <= set(every):
        raise ValueError(
            f"dataset condition covers {len(conditions)} day(s); expected one "
            f"per day of [{start}, {end}) (a Saturday may be absent)"
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


# -- batch jobs and files --------------------------------------------------------

_SHA256_PREFIX = "sha256"


def _optional[T](raw: object, parse: Callable[[object], T]) -> T | None:
    return None if raw is None else parse(raw)


def _day(raw: object, what: str) -> date:
    """A job range bound, which this adapter always submits at UTC midnight."""
    moment = _utc_datetime(raw, what)
    if moment.timetz().replace(tzinfo=None) != time():
        raise ValueError(f"{what} {raw!r} is not a UTC day boundary")
    return moment.date()


def _symbols(raw: object) -> tuple[str, ...]:
    """The job's symbols: a comma-joined string, or a list of strings."""
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    return _str_tuple(raw, "symbols")


def parse_job_id(raw: object) -> str:
    return _as_str(_as_mapping(raw, "batch job")["id"], "job id")


def parse_job_ids(raw: object) -> tuple[str, ...]:
    return tuple(parse_job_id(item) for item in _as_list(raw, "batch jobs"))


def parse_batch_job(raw: object) -> BatchJob:
    body = _as_mapping(raw, "batch job")
    request = TickRequest(
        dataset=_as_str(body["dataset"], "dataset"),
        symbols=_symbols(body["symbols"]),
        stype_in=SType(_as_str(body["stype_in"], "stype_in")),
        schema=TickSchema(_as_str(body["schema"], "schema")),
        start=_day(body["start"], "job start"),
        end=_day(body["end"], "job end"),
    )
    return BatchJob(
        job_id=_as_str(body["id"], "job id"),
        request=request,
        state=BatchJobState(_as_str(body["state"], "state")),
        ts_received=_utc_datetime(body["ts_received"], "ts_received"),
        ts_expiration=_optional(
            body["ts_expiration"], lambda v: _utc_datetime(v, "ts_expiration")
        ),
        record_count=_optional(body["record_count"], lambda v: as_count(v, "records")),
        billed_size=_optional(body["billed_size"], lambda v: as_count(v, "billed")),
        actual_size=_optional(body["actual_size"], lambda v: as_count(v, "actual")),
        package_size=_optional(body["package_size"], lambda v: as_count(v, "package")),
        cost_usd=_optional(body["cost_usd"], as_usd),
    )


def _batch_file(raw: object) -> BatchFile:
    body = _as_mapping(raw, "batch file")
    filename = _as_str(body["filename"], "filename")
    if Path(filename).name != filename:
        raise ValueError(f"batch file name {filename!r} is not a plain file name")
    algorithm, _, digest = _as_str(body["hash"], "hash").partition(":")
    if algorithm != _SHA256_PREFIX or not digest:
        raise ValueError(f"batch file {filename}: unsupported hash {algorithm!r}")
    return BatchFile(
        filename=filename,
        size=as_count(body["size"], "file size"),
        sha256=digest.lower(),
        url=_as_str(_as_mapping(body["urls"], "urls")["https"], "https url"),
    )


def parse_batch_files(raw: object) -> tuple[BatchFile, ...]:
    return tuple(_batch_file(item) for item in _as_list(raw, "batch files"))
