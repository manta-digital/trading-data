"""DBN file reader over the committed databento/dbn sample files (slice 220).

Expected values come from decoding the files themselves; see
``test/fixtures/databento/SOURCES.md`` for provenance.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import databento_dbn
import pytest
from tick_support.dbn_files import BAD_HEADERS, bad_header_bytes, day_file_bytes
from tick_support.tier_units import TRADES_DAY, real_file

from manta_trading.data.tick import constants
from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema
from manta_trading.data.tick.databento.dbn_file import (
    DbnFile,
    DbnFileReader,
    _as_date,
)
from manta_trading.data.tick.provider import SymbolInterval, TickFileDecodeError

FIXTURES = Path(__file__).resolve().parents[4] / "test" / "fixtures" / "databento"

ESH1 = {"ESH1": (SymbolInterval(date(2020, 12, 28), date(2020, 12, 29), 5482),)}

#: file, schema, native itemsize, first record's price (fixed-point, 1e-9).
ES_FILES = [
    ("test_data.trades.v3.dbn.zst", TickSchema.TRADES, 48, 3720250000000),
    ("test_data.tbbo.v3.dbn.zst", TickSchema.TBBO, 80, 3720250000000),
    ("test_data.mbp-1.v3.dbn.zst", TickSchema.MBP_1, 80, 3720500000000),
    ("test_data.trades.v2.dbn.zst", TickSchema.TRADES, 48, 3720250000000),
]


def _open(name: str) -> DbnFile:
    return DbnFileReader().open_file(FIXTURES / name)


@pytest.mark.parametrize(("name", "schema", "itemsize", "price"), ES_FILES)
def test_es_header(name: str, schema: TickSchema, itemsize: int, price: int) -> None:
    tick_file = _open(name)
    assert tick_file.dataset == CME_DATASET
    assert tick_file.schema is schema
    assert tick_file.stype_in is SType.RAW_SYMBOL
    assert dict(tick_file.mappings) == ESH1
    assert tick_file.partial == ()
    assert tick_file.not_found == ()
    assert tick_file.start == datetime(2020, 12, 28, 13, tzinfo=UTC)
    assert tick_file.end == datetime(2020, 12, 29, tzinfo=UTC)


@pytest.mark.parametrize(("name", "schema", "itemsize", "price"), ES_FILES)
def test_es_batches(name: str, schema: TickSchema, itemsize: int, price: int) -> None:
    batches = list(_open(name).iter_batches())
    assert sum(b.count for b in batches) == 2
    first = batches[0]
    assert first.schema is schema
    assert first.records.dtype.itemsize == itemsize
    assert first.count == len(first.records)
    assert int(first.records["price"][0]) == price
    assert int(first.records["instrument_id"][0]) == 5482


def test_v2_trades_match_v3() -> None:
    v2 = next(_open("test_data.trades.v2.dbn.zst").iter_batches()).records
    v3 = next(_open("test_data.trades.v3.dbn.zst").iter_batches()).records
    assert v2.dtype.itemsize == v3.dtype.itemsize
    assert v2.tobytes() == v3.tobytes()


def test_definition_file() -> None:
    """XNAS.ITCH (MSFT), not CME: record shape only."""
    tick_file = _open("test_data.definition.v3.dbn.zst")
    assert tick_file.schema is TickSchema.DEFINITION
    batches = list(tick_file.iter_batches())
    assert batches[0].records.dtype.itemsize == 520
    assert sum(b.count for b in batches) == 2
    assert all(
        isinstance(i.instrument_id, int)
        for intervals in tick_file.mappings.values()
        for i in intervals
    )


@pytest.mark.parametrize(("name", "schema", "itemsize", "price"), ES_FILES)
def test_byte_budget_bounds_every_batch(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    schema: TickSchema,
    itemsize: int,
    price: int,
) -> None:
    monkeypatch.setattr(constants, "TICK_DECODE_BATCH_BYTES", itemsize)
    batches = list(_open(name).iter_batches())
    assert len(batches) == 2
    assert all(b.records.nbytes <= itemsize for b in batches)


def test_default_budget_bounds_batches() -> None:
    for batch in _open("test_data.tbbo.v3.dbn.zst").iter_batches():
        assert batch.records.nbytes <= constants.TICK_DECODE_BATCH_BYTES


def test_budget_below_one_record_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, "TICK_DECODE_BATCH_BYTES", 47)
    with pytest.raises(ValueError, match="below one trades record"):
        list(_open("test_data.trades.v3.dbn.zst").iter_batches())


def test_unknown_schema_raises_naming_it(tmp_path: Path) -> None:
    """An OHLCV file is a valid DBN file this reader does not accept."""
    path = tmp_path / "ohlcv.dbn"
    metadata = databento_dbn.Metadata(
        dataset=CME_DATASET,
        schema=databento_dbn.Schema("ohlcv-1m"),
        start=0,
        stype_in=databento_dbn.SType(SType.RAW_SYMBOL.value),
        stype_out=databento_dbn.SType(SType.INSTRUMENT_ID.value),
        ts_out=False,
    )
    path.write_bytes(bytes(metadata.encode()))
    with pytest.raises(TickFileDecodeError, match="unsupported DBN schema 'ohlcv-1m'"):
        DbnFileReader().open_file(path)


#: Each header refusal and the message it keeps (TD 8: one bad file fails its
#: unit, not the pass, so every one is a ``TickFileDecodeError``).
BAD_HEADER_MESSAGES = {
    "mixed": "DBN file has mixed record types",
    "schema": "unsupported DBN schema 'ohlcv-1h'",
    "stype_in": "unsupported stype_in 'isin'",
    "stype_out": "stype_out is raw_symbol, expected instrument_id",
    "ts_out": "ts_out records are not supported",
    "mapping_date": "parsing start date of mapping interval",
}


def test_every_bad_header_has_a_message() -> None:
    assert set(BAD_HEADER_MESSAGES) == set(BAD_HEADERS)


#: A synthetic v3 day file and a real v1 archive slice: the header layouts
#: differ, and the archive holds v1.
def _v3_day_file() -> bytes:
    return day_file_bytes(
        "test_data.trades.v3.dbn.zst", CME_DATASET, TRADES_DAY, SType.PARENT
    )


def _v1_real_file() -> bytes:
    return real_file(TRADES_DAY, TickSchema.TRADES).read_bytes()


@pytest.mark.parametrize("source", [_v3_day_file, _v1_real_file])
@pytest.mark.parametrize("kind", BAD_HEADERS)
def test_bad_header_raises_decode_error(
    tmp_path: Path, kind: str, source: Callable[[], bytes]
) -> None:
    path = tmp_path / f"{kind}.dbn.zst"
    path.write_bytes(bad_header_bytes(source(), kind))
    with pytest.raises(TickFileDecodeError, match=BAD_HEADER_MESSAGES[kind]):
        DbnFileReader().open_file(path)


def test_mapping_date_of_wrong_type_raises_decode_error() -> None:
    """The SDK always yields ``date``; this guards a change in that contract."""
    with pytest.raises(TickFileDecodeError, match="mapping date is str"):
        _as_date("2024-09-03")
