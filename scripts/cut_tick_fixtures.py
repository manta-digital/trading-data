"""Cut small real DBN slices from the tick archive as test fixtures (slice 225).

Each tier slice keeps the provider's header bytes unchanged (it spans exactly
the unit's UTC day) and a subset of the day's records, in file order:

- the first records after 00:00 UTC;
- the records on both sides of the daily break (the largest ``ts_event`` gap),
  which puts two trading sessions in one file;
- the last records before 24:00 UTC;
- up to ``PER_OTHER_ID`` records of every instrument but the busiest, which
  keeps the calendar spreads and the back months.

The definition file for each day is copied whole (a few KB).

The supersession fixture (FR6) is a ``trades`` file for the ``tbbo`` slice's
day. The adopted jobs share no day, so it is derived: each ``tbbo`` record's
leading trade fields become a ``trades`` record (the same events), and the
header's schema field is rewritten to ``trades``.

The bar fixture (slice 302) is derived the same way, from the day's *whole*
``tbbo`` file: trade-only one-minute bars per instrument and minute
(``ts_event`` at the minute's open; open/close the first/last trade, high/low
the extremes, volume the summed sizes), under the ``tbbo`` header with its
schema field set to ``ohlcv-1m``. It makes no claim of provider parity.

Manual, developer-run only. Output lands in ``test/fixtures/databento/real/``.
Usage::

    uv run python scripts/cut_tick_fixtures.py [--archive /data/tick-archive]
"""

from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

import databento
import databento_dbn
import numpy as np
import zstandard

OUT = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "databento" / "real"

#: (job id, file name) of each tier slice and its definition file.
TIER_CUTS = (
    ("GLBX-20240930-USM7UXXJBA", "glbx-mdp3-20240903.trades.dbn.zst"),
    ("GLBX-20250123-XT4GD5UM6C", "glbx-mdp3-20241203.tbbo.dbn.zst"),
)
DEFINITIONS = (
    ("GLBX-20260930-DLDYL5DM8Q", "glbx-mdp3-20240903.definition.dbn.zst"),
    ("GLBX-20260930-HVGRLYKHRN", "glbx-mdp3-20241203.definition.dbn.zst"),
)
DERIVED_TRADES = "glbx-mdp3-20241203.trades.dbn.zst"
DERIVED_BARS = "glbx-mdp3-20241203.ohlcv-1m.dbn.zst"

HEAD, BREAK_SIDE, TAIL, PER_OTHER_ID = 1000, 1000, 500, 100

#: DBN prelude: ``DBN`` + version byte, then the metadata length (u32 LE).
_PRELUDE = struct.Struct("<4sI")
#: Metadata starts with the 16-byte dataset, then the schema as u16 LE.
_SCHEMA_OFFSET = _PRELUDE.size + 16
#: A ``trades`` record is the leading 48 bytes of an ``mbp-1``/``tbbo`` record
#: (record header, price, size, action, side, flags, depth, ts_recv,
#: ts_in_delta, sequence); byte 0 is its length in 4-byte words, byte 1 its rtype.
_TRADE_SIZE = 48
_RTYPE_MBP_0 = 0x00
#: An ``ohlcv-1m`` record: the record header, then open/high/low/close and
#: volume. Length is 14 four-byte words (56 bytes); the rtype is 0x21.
_BAR_DTYPE = np.dtype(
    [
        ("length", "u1"),
        ("rtype", "u1"),
        ("publisher_id", "<u2"),
        ("instrument_id", "<u4"),
        ("ts_event", "<u8"),
        ("open", "<i8"),
        ("high", "<i8"),
        ("low", "<i8"),
        ("close", "<i8"),
        ("volume", "<u8"),
    ]
)
_RTYPE_OHLCV_1M = 0x21
_NS_PER_MINUTE = 60 * 1_000_000_000


def _split(path: Path) -> tuple[bytes, bytes]:
    """The file's header bytes (prelude + metadata) and its record bytes."""
    raw = zstandard.ZstdDecompressor().stream_reader(path.read_bytes()).read()
    _, length = _PRELUDE.unpack_from(raw)
    end = _PRELUDE.size + length
    return raw[:end], raw[end:]


def _keep(records: np.ndarray) -> np.ndarray:
    """Boolean mask over the day's records: the windows the docstring names."""
    count = len(records)
    keep = np.zeros(count, dtype=bool)
    keep[:HEAD] = True
    keep[-TAIL:] = True
    gap = int(np.argmax(np.diff(records["ts_event"].astype(np.int64))))
    keep[max(gap + 1 - BREAK_SIDE, 0) : gap + 1 + BREAK_SIDE] = True
    ids, counts = np.unique(records["instrument_id"], return_counts=True)
    for instrument in ids[ids != ids[np.argmax(counts)]]:
        keep[np.flatnonzero(records["instrument_id"] == instrument)[:PER_OTHER_ID]] = (
            True
        )
    return keep


def _write(path: Path, header: bytes, records: bytes) -> None:
    path.write_bytes(zstandard.ZstdCompressor().compress(header + records))
    print(f"{path.name}: {path.stat().st_size} bytes")


def _cut(source: Path) -> tuple[bytes, np.ndarray]:
    header, body = _split(source)
    decoded = databento.DBNStore.from_file(source).to_ndarray()
    size = len(body) // len(decoded)
    if size * len(decoded) != len(body) or body[0] * 4 != size:
        raise ValueError(f"{source}: records are not fixed-size {size}-byte records")
    raw = np.frombuffer(body, dtype=f"V{size}")[_keep(decoded)]
    return header, raw


def _derive_trades(header: bytes, tbbo: np.ndarray) -> tuple[bytes, bytes]:
    """``trades`` records from ``tbbo`` records; writes the new header's schema."""
    rows = np.frombuffer(tbbo.tobytes(), dtype=np.uint8).reshape(len(tbbo), -1)
    trades = rows[:, :_TRADE_SIZE].copy()
    trades[:, 0] = _TRADE_SIZE // 4
    trades[:, 1] = _RTYPE_MBP_0
    patched = bytearray(header)
    struct.pack_into("<H", patched, _SCHEMA_OFFSET, int(databento_dbn.Schema.TRADES))
    return bytes(patched), trades.tobytes()


def _derive_bars(header: bytes, decoded: np.ndarray) -> tuple[bytes, bytes]:
    """One-minute trade bars from a whole ``tbbo`` day; the header's schema is
    rewritten to ``ohlcv-1m``. Bars are ordered by minute, then instrument."""
    ts = decoded["ts_event"].astype(np.int64)
    minute = ts - ts % _NS_PER_MINUTE
    ids = decoded["instrument_id"].astype(np.int64)
    # Stable sort: records of one (minute, instrument) stay in file order, so
    # the first and last of a group are its open and close.
    order = np.lexsort((ids, minute))
    key_minute, key_id = minute[order], ids[order]
    starts = np.flatnonzero(
        np.diff(key_minute, prepend=-1) | np.diff(key_id, prepend=-1)
    )
    price = decoded["price"].astype(np.int64)[order]
    size = decoded["size"].astype(np.uint64)[order]
    ends = np.append(starts[1:], len(order)) - 1
    bars = np.zeros(len(starts), dtype=_BAR_DTYPE)
    bars["length"] = _BAR_DTYPE.itemsize // 4
    bars["rtype"] = _RTYPE_OHLCV_1M
    bars["publisher_id"] = decoded["publisher_id"][order][starts]
    bars["instrument_id"] = key_id[starts]
    bars["ts_event"] = key_minute[starts]
    bars["open"] = price[starts]
    bars["close"] = price[ends]
    bars["high"] = np.maximum.reduceat(price, starts)
    bars["low"] = np.minimum.reduceat(price, starts)
    bars["volume"] = np.add.reduceat(size, starts)
    patched = bytearray(header)
    struct.pack_into("<H", patched, _SCHEMA_OFFSET, int(databento_dbn.Schema.OHLCV_1M))
    return bytes(patched), bars.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--archive", type=Path, default=Path("/data/tick-archive"))
    archive = parser.parse_args().archive
    OUT.mkdir(parents=True, exist_ok=True)
    cut: dict[str, tuple[bytes, np.ndarray]] = {}
    for job, name in TIER_CUTS:
        cut[name] = _cut(archive / job / name)
        _write(OUT / name, cut[name][0], cut[name][1].tobytes())
    header, tbbo = cut[TIER_CUTS[1][1]]
    _write(OUT / DERIVED_TRADES, *_derive_trades(header, tbbo))
    job, name = TIER_CUTS[1]
    whole = archive / job / name
    bars = _derive_bars(
        _split(whole)[0], databento.DBNStore.from_file(whole).to_ndarray()
    )
    _write(OUT / DERIVED_BARS, *bars)
    for job, name in DEFINITIONS:
        shutil.copyfile(archive / job / name, OUT / name)


if __name__ == "__main__":
    main()
