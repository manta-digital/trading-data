"""DBN day files and batch job directories built from the committed fixtures (223).

The fixtures' headers do not span one UTC day (trades and tbbo cover
2020-12-28 13:00 → 2020-12-29 00:00 UTC; the definition file is XNAS over
months). Verification requires a header spanning exactly the unit's UTC day
(LLD Technical Decision 9, Verification), so ``write_day_file`` re-encodes the
fixture's header with the wanted dataset, day and ``stype_in`` and appends the
fixture's record bytes unchanged. No purchased data is committed as a fixture.

``write_job_dir`` lays files out as a provider batch job (one directory,
``manifest.json`` listing every other file); ``zip_job_dir`` packs it the way
the provider's download zip is laid out.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import databento_dbn
import zstandard

from manta_trading.data.tick.constants import SType

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "databento"

#: DBN prelude: ``DBN`` + version byte, then the metadata length (u32 LE).
_PRELUDE = struct.Struct("<4sI")

#: The JSON files a provider job carries beside its data files.
JOB_JSON_FILES = ("condition.json", "metadata.json", "symbology.json")
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class JobFile:
    """One file of a job directory, as ``manifest.json`` lists it."""

    path: Path
    size: int
    sha256: str


def _ns(day: date) -> int:
    return int(datetime.combine(day, time(), UTC).timestamp()) * 1_000_000_000


def _mappings(metadata: databento_dbn.Metadata) -> list[SimpleNamespace]:
    """The decoded mapping dicts as the ``SymbolMapping`` shape ``Metadata`` takes."""
    return [
        SimpleNamespace(
            raw_symbol=symbol,
            intervals=[SimpleNamespace(**interval) for interval in intervals],
        )
        for symbol, intervals in metadata.mappings.items()
    ]


def day_file_bytes(fixture: str, dataset: str, day: date, stype_in: SType) -> bytes:
    """The fixture's records under a header spanning exactly ``day`` (UTC)."""
    compressed = (FIXTURES / fixture).read_bytes()
    raw = zstandard.ZstdDecompressor().stream_reader(compressed).read()
    _, length = _PRELUDE.unpack_from(raw)
    records = raw[_PRELUDE.size + length :]
    source = databento_dbn.Metadata.decode(raw)
    if source.schema is None:
        raise ValueError(f"{fixture}: mixed-schema fixture, no header schema")
    header = databento_dbn.Metadata(
        dataset=dataset,
        start=_ns(day),
        end=_ns(day + timedelta(days=1)),
        stype_in=databento_dbn.SType(stype_in.value),
        stype_out=source.stype_out,
        schema=databento_dbn.Schema(source.schema),
        symbols=list(source.symbols),
        partial=list(source.partial),
        not_found=list(source.not_found),
        mappings=_mappings(source),
        version=source.version,
    )
    return zstandard.ZstdCompressor().compress(header.encode() + records)


def _write(path: Path, content: bytes) -> JobFile:
    path.write_bytes(content)
    return JobFile(path, len(content), hashlib.sha256(content).hexdigest())


def write_day_file(
    dest: Path, fixture: str, dataset: str, day: date, stype_in: SType
) -> JobFile:
    """Write one day file at ``dest``; return its path, size and SHA-256."""
    return _write(dest, day_file_bytes(fixture, dataset, day, stype_in))


def write_job_dir(root: Path, job_id: str, files: dict[str, bytes]) -> Path:
    """``<root>/<job_id>/`` with ``files``, JSON placeholders and ``manifest.json``.

    ``manifest.json`` lists every other file (``filename``, ``size``, ``hash``
    as ``sha256:<hex>``, ``urls``) but not itself, as the provider writes it.
    """
    job_dir = root / job_id
    job_dir.mkdir(parents=True)
    contents = dict(files)
    for name in JOB_JSON_FILES:
        contents.setdefault(name, json.dumps({"placeholder": name}).encode())
    listed = [
        {
            "filename": name,
            "size": written.size,
            "hash": f"sha256:{written.sha256}",
            "urls": {"https": f"https://example.invalid/{job_id}/{name}"},
        }
        for name, content in sorted(contents.items())
        for written in (_write(job_dir / name, content),)
    ]
    manifest = {"job_id": job_id, "files": listed}
    (job_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    return job_dir


def zip_job_dir(job_dir: Path) -> Path:
    """Zip ``job_dir`` as ``<job_dir>.zip``, members flat at the zip root.

    The provider's zip (``GLBX-20250123-XT4GD5UM6C.zip``) has no directory
    prefix: ``condition.json``, the day files, then ``manifest.json``.
    """
    archive = job_dir.with_name(job_dir.name + ".zip")
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(job_dir.iterdir()):
            zf.write(path, path.name)
    return archive
