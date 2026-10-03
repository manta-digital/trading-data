"""DBN file reader: ``ITickFileReader`` / ``ITickFile`` over the SDK's ``DBNStore``.

The only module that reads a DBN byte. Decode uses the SDK's array path,
``DBNStore.to_ndarray(count=N)``: zstd decompression with the interpreter
lock released, then a ``numpy.frombuffer`` view per batch — the path design
Technical Decision 6 runs in worker threads.

Two SDK behaviours callers rely on knowing:

- The array path decodes records in the file's **own** DBN version (a v2
  file yields v2-layout arrays); it does not upgrade. The record size, and so
  the batch count, is taken from the same per-version struct map the SDK
  uses.
- The SDK's decode failures (``DBNError``, ``BentoError``) are raised as
  ``TickFileDecodeError``, as is every refusal of header content (mixed
  records, unknown schema or ``stype_in``, wrong ``stype_out``, ``ts_out``,
  a mapping date of the wrong type): one bad file fails its unit, not the
  pass. A missing file stays ``FileNotFoundError``; the batch-budget
  ``ValueError`` is a configuration error and stops the run.
- A file whose record bytes end mid-record yields a short final batch and
  only a ``BentoWarning``. The adapter's file-name rule (a final name is a
  completed, and for batch files checksum-verified, download) is what
  excludes truncated files; this reader does not re-detect them.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import databento
from databento.common.constants import (
    SCHEMA_STRUCT_MAP,
    SCHEMA_STRUCT_MAP_V1,
    SCHEMA_STRUCT_MAP_V2,
)
from databento.common.error import BentoError
from databento_dbn import DBNError, Schema

from manta_trading.data.tick import constants
from manta_trading.data.tick.constants import SType, TickSchema
from manta_trading.data.tick.provider import (
    RecordBatch,
    SymbolInterval,
    TickFileDecodeError,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NS_PER_US = 1_000

#: The SDK's own version → struct-map choice (``DBNStore._schema_struct_map``);
#: versions ≥ 3 use the current map.
_STRUCT_MAP_BY_VERSION = {1: SCHEMA_STRUCT_MAP_V1, 2: SCHEMA_STRUCT_MAP_V2}

_KNOWN_SCHEMAS = frozenset(s.value for s in TickSchema)
_KNOWN_STYPES = frozenset(s.value for s in SType)


def _utc_from_ns(nanoseconds: int) -> datetime:
    """DBN header timestamp (ns since epoch) → aware UTC, microsecond grain."""
    return _EPOCH + timedelta(microseconds=nanoseconds // _NS_PER_US)


def _tick_schema(store: databento.DBNStore, path: Path) -> TickSchema:
    raw = store.schema
    if raw is None:
        raise TickFileDecodeError(
            f"{path}: DBN file has mixed record types (no schema)"
        )
    if raw.value not in _KNOWN_SCHEMAS:
        raise TickFileDecodeError(f"{path}: unsupported DBN schema {raw.value!r}")
    return TickSchema(raw.value)


def _stype_in(value: object, path: Path) -> SType:
    text = str(value)
    if text not in _KNOWN_STYPES:
        raise TickFileDecodeError(f"{path}: unsupported stype_in {text!r}")
    return SType(text)


def _mappings(
    raw: Mapping[str, Sequence[Mapping[str, object]]], stype_out: object, path: Path
) -> dict[str, tuple[SymbolInterval, ...]]:
    """Header mappings → ``SymbolInterval`` with integer instrument ids.

    Every request is sent with ``stype_out=instrument_id`` (TD 9), so a file
    mapping to anything else is refused rather than half-parsed.
    """
    if str(stype_out) != SType.INSTRUMENT_ID:
        raise TickFileDecodeError(
            f"{path}: stype_out is {stype_out!s}, expected {SType.INSTRUMENT_ID}"
        )
    result: dict[str, tuple[SymbolInterval, ...]] = {}
    for symbol, intervals in raw.items():
        result[symbol] = tuple(
            SymbolInterval(
                start_date=_as_date(item["start_date"]),
                end_date=_as_date(item["end_date"]),
                instrument_id=int(str(item["symbol"])),
            )
            for item in intervals
        )
    return result


def _as_date(value: object) -> date:
    if not isinstance(value, date):
        raise TickFileDecodeError(
            f"mapping date is {type(value).__name__}, expected date"
        )
    return value


def _record_size(store: databento.DBNStore, schema: TickSchema) -> int:
    """Native record size in bytes for the file's DBN version and schema."""
    struct_map = _STRUCT_MAP_BY_VERSION.get(store.metadata.version, SCHEMA_STRUCT_MAP)
    return int(struct_map[Schema(schema.value)].size_hint)


class DbnFile:
    """``ITickFile`` over one DBN file. One thread; owns its ``DBNStore``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            store = databento.DBNStore.from_file(path)
        except (DBNError, BentoError) as exc:
            raise TickFileDecodeError(f"{path}: {exc}") from exc
        metadata = store.metadata
        if metadata.ts_out:
            # Live-only framing; the SDK's array path ignores the extra field
            # and would mis-slice every record.
            raise TickFileDecodeError(f"{path}: ts_out records are not supported")
        self._store = store
        self.dataset: str = str(metadata.dataset)
        self.schema: TickSchema = _tick_schema(store, path)
        self.stype_in: SType = _stype_in(metadata.stype_in, path)
        self.start: datetime = _utc_from_ns(metadata.start)
        self.end: datetime = _utc_from_ns(metadata.end)
        self.mappings: Mapping[str, tuple[SymbolInterval, ...]] = _mappings(
            metadata.mappings, metadata.stype_out, path
        )
        self.partial: tuple[str, ...] = tuple(metadata.partial)
        self.not_found: tuple[str, ...] = tuple(metadata.not_found)
        self.record_size: int = _record_size(store, self.schema)

    def iter_batches(self) -> Iterator[RecordBatch]:
        """Yield every record in batches of at most ``TICK_DECODE_BATCH_BYTES``.

        The budget is read at call time so tests can patch it.
        """
        budget = constants.TICK_DECODE_BATCH_BYTES
        records_per_batch = budget // self.record_size
        if records_per_batch == 0:
            raise ValueError(
                f"TICK_DECODE_BATCH_BYTES={budget} is below one {self.schema} "
                f"record ({self.record_size} bytes)"
            )
        try:
            for records in self._store.to_ndarray(count=records_per_batch):
                yield RecordBatch(
                    schema=self.schema, records=records, count=len(records)
                )
        except (DBNError, BentoError) as exc:
            raise TickFileDecodeError(f"{self._path}: {exc}") from exc


class DbnFileReader:
    """``ITickFileReader``: stateless, shareable across threads."""

    def open_file(self, path: Path) -> DbnFile:
        return DbnFile(path)
