"""DBN file reader over the committed databento/dbn sample files (slice 220).

Expected values come from decoding the files themselves; see
``test/fixtures/databento/SOURCES.md`` for provenance.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import databento_dbn
import pytest

from manta_trading.data.tick import constants
from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema
from manta_trading.data.tick.databento.dbn_file import DbnFile, DbnFileReader
from manta_trading.data.tick.provider import SymbolInterval

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
    with pytest.raises(ValueError, match="unsupported DBN schema 'ohlcv-1m'"):
        DbnFileReader().open_file(path)
