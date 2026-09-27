"""DBN decode microbenchmark: per-record path vs array path, 1 vs N threads.

Answers slice 220's interpreter-lock question empirically (design Technical
Decisions 6 and 12). The input is built in memory from the committed real
sample ``test_data.trades.v3.dbn.zst``: its real header followed by its two
real trade records repeated to ``--records``, zstd-compressed as delivered
files are, and written to a temporary file.

- **per-record**: iterate ``DBNStore`` (the SDK's Rust ``DBNDecoder``, which
  holds the GIL for each decode call), one Python object per record.
- **array**: ``DbnFileReader().open_file(path).iter_batches()`` — the
  production path — ``to_ndarray(count=N)`` over a zstd stream reader, with
  batches bounded by ``TICK_DECODE_BATCH_BYTES``.

Each thread decodes its own full copy of the file, so N threads do N times the
work; speedup = (N-thread records/s) / (1-thread records/s).

Throughput caveat: this measures **decode only**, on repeated records, with
no ``COPY`` and no real session volume. Its rate is an upper bound on ingest
throughput: it can fail the architecture's ingest target (a decode rate
already too slow), never pass it. Slice 226 decides the target on purchased
data.

Usage::

    uv run python scripts/bench_dbn_decode.py --records 2000000 --threads 4
"""

from __future__ import annotations

import argparse
import platform
import struct
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import databento
import zstandard

from manta_trading.data.tick.databento.dbn_file import DbnFileReader

SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "test"
    / "fixtures"
    / "databento"
    / "test_data.trades.v3.dbn.zst"
)
#: DBN prefix: b"DBN", a version byte, then the metadata length as u32 LE.
_PREFIX = struct.Struct("<3sBI")


def build_input(records: int, directory: Path) -> Path:
    """Real header + real records repeated to ``records``, zstd-compressed."""
    with SAMPLE.open("rb") as handle:
        raw = zstandard.ZstdDecompressor().stream_reader(handle).read()
    magic, _version, metadata_length = _PREFIX.unpack_from(raw)
    if magic != b"DBN":
        raise ValueError(f"{SAMPLE} is not a DBN file")
    header_end = _PREFIX.size + metadata_length
    header, body = raw[:header_end], raw[header_end:]
    record_size = DbnFileReader().open_file(SAMPLE).record_size
    sample_count = len(body) // record_size
    if sample_count == 0:
        raise ValueError(f"{SAMPLE} holds no records to repeat")
    repeats, remainder = divmod(records, sample_count)
    payload = header + body * repeats + body[: remainder * record_size]
    path = directory / "bench.trades.dbn.zst"
    path.write_bytes(zstandard.ZstdCompressor(level=3).compress(payload))
    return path


def decode_per_record(path: Path) -> int:
    return sum(1 for _ in databento.DBNStore.from_file(path))


def decode_array(path: Path) -> int:
    return sum(batch.count for batch in DbnFileReader().open_file(path).iter_batches())


def run(decode: Callable[[Path], int], path: Path, threads: int) -> tuple[float, int]:
    """Wall time for ``threads`` concurrent full decodes; total records."""
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        counts = list(pool.map(decode, [path] * threads))
    return time.perf_counter() - started, sum(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--records", type=int, default=2_000_000)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.records < 1 or args.threads < 2:
        parser.error("--records must be ≥ 1 and --threads ≥ 2")

    print(
        f"host: {platform.node()}  python: {sys.version.split()[0]}  "
        f"databento: {databento.__version__}"
    )
    print(f"records per decode: {args.records:,}  threads: 1 vs {args.threads}")
    print()
    print(f"{'path':<11} {'threads':>7} {'wall s':>8} {'records/s':>14} {'speedup':>8}")
    with tempfile.TemporaryDirectory() as tmp:
        path = build_input(args.records, Path(tmp))
        decoders = (("per-record", decode_per_record), ("array", decode_array))
        for name, decode in decoders:
            base_rate = 0.0
            for threads in (1, args.threads):
                wall, total = run(decode, path, threads)
                if total != args.records * threads:
                    raise RuntimeError(f"{name} decoded {total:,} records")
                rate = total / wall
                base_rate = base_rate or rate
                print(
                    f"{name:<11} {threads:>7} {wall:>8.2f} {rate:>14,.0f} "
                    f"{rate / base_rate:>7.2f}x"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
