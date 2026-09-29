"""Adoption's file handling: a job directory or zip → ``<archive>/<job_id>/`` (223).

LLD 224 Technical Decision 10. ``manifest.json`` (from the directory or the
zip) lists every other file of the job with its size and ``sha256:<hex>``
hash, but not itself. Each listed file, and ``manifest.json`` itself, is
written as ``<name>.partial``, hashed while written, and renamed only when
its size and SHA-256 match. A file already present under its final name with
a matching hash is skipped, so adopting from the archive itself copies
nothing.

Refusals (:class:`TickAdoptionRefused`, exit 1) write no manifest row and
leave ``.partial`` files for inspection: a job-id mismatch, a listed name
that could escape the job directory, a missing member, a size or hash
mismatch, or too little free space. A write failure (for example
``ENOSPC``) is a host fault, not a refusal: :class:`TickArchiveWriteError`,
exit 4. Every call here blocks; the caller runs it in a thread.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from manta_trading.data.tick.hashing import HASH_CHUNK_BYTES, sha256_file

MANIFEST_NAME = "manifest.json"
PARTIAL_SUFFIX = ".partial"
_HASH_PREFIX = "sha256:"
_UNSAFE_NAME_PARTS = ("/", "\\", "..")

FreeBytes = Callable[[Path], int]


class TickAdoptionRefused(Exception):
    """Adoption refused before any manifest row was written (exit 1)."""


class TickArchiveWriteError(Exception):
    """Writing into the archive failed: a host fault, not a unit fault (exit 4)."""

    def __init__(self, path: Path, exc: OSError) -> None:
        self.path = path
        self.errno = exc.errno
        super().__init__(f"cannot write {path}: [errno {exc.errno}] {exc.strerror}")


@dataclass(frozen=True)
class ListedFile:
    """One file ``manifest.json`` lists."""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ArchivedFile:
    """A file now in the archive; ``copied`` is False when it was already there."""

    name: str
    size: int
    sha256: str
    copied: bool


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


class _Source:
    """Reads members of a job directory or the provider's (flat) zip by name."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip = None if path.is_dir() else _open_zip(path)

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    @contextmanager
    def open(self, name: str) -> Iterator[IO[bytes]]:
        try:
            if self._zip is None:
                with (self.path / name).open("rb") as handle:
                    yield handle
            else:
                with self._zip.open(name) as handle:
                    yield handle
        except (FileNotFoundError, KeyError) as exc:
            raise TickAdoptionRefused(
                f"{name} is listed but not in {self.path}"
            ) from exc
        except zipfile.BadZipFile as exc:  # a corrupt member, e.g. a CRC mismatch
            raise TickAdoptionRefused(f"{name} in {self.path}: {exc}") from exc


def _open_zip(path: Path) -> zipfile.ZipFile:
    if not zipfile.is_zipfile(path):
        raise TickAdoptionRefused(f"{path} is neither a job directory nor a zip")
    return zipfile.ZipFile(path)


def _listed(entry: dict[str, Any]) -> ListedFile:
    name = str(entry["filename"])
    if not name or any(part in name for part in _UNSAFE_NAME_PARTS):
        raise TickAdoptionRefused(f"manifest.json lists an unsafe name {name!r}")
    digest = str(entry["hash"])
    if not digest.startswith(_HASH_PREFIX):
        raise TickAdoptionRefused(f"{name}: unsupported hash {digest!r}")
    sha256 = digest.removeprefix(_HASH_PREFIX).lower()
    return ListedFile(name, int(entry["size"]), sha256)


def _read_manifest(source: _Source, job_id: str) -> tuple[bytes, list[ListedFile]]:
    with source.open(MANIFEST_NAME) as handle:
        raw = handle.read()
    try:
        body = json.loads(raw)
        listed_job, entries = body["job_id"], body["files"]
    except (ValueError, KeyError, TypeError) as exc:
        raise TickAdoptionRefused(f"{MANIFEST_NAME} is unreadable: {exc!r}") from exc
    if listed_job != job_id:
        raise TickAdoptionRefused(
            f"{MANIFEST_NAME} is for job {listed_job}, not --job-id {job_id}"
        )
    return raw, [_listed(entry) for entry in entries]


def _already_archived(final: Path, listed: ListedFile) -> bool:
    """True when ``final`` is this file; refuses when it is some other file."""
    if not final.exists():
        return False
    if final.stat().st_size == listed.size and sha256_file(final) == listed.sha256:
        return True
    raise TickAdoptionRefused(f"{final} exists but does not match {MANIFEST_NAME}")


def _write_partial(source: _Source, listed: ListedFile, partial: Path) -> str:
    """Stream the member into ``partial``; return the SHA-256 of what was written."""
    digest = hashlib.sha256()
    with source.open(listed.name) as reader:
        try:
            with partial.open("wb") as out:
                while chunk := reader.read(HASH_CHUNK_BYTES):
                    digest.update(chunk)
                    out.write(chunk)
        except OSError as exc:
            raise TickArchiveWriteError(partial, exc) from exc
    return digest.hexdigest()


def _copy(source: _Source, listed: ListedFile, job_dir: Path) -> ArchivedFile:
    final = job_dir / listed.name
    if _already_archived(final, listed):
        return ArchivedFile(listed.name, listed.size, listed.sha256, copied=False)
    partial = final.with_name(final.name + PARTIAL_SUFFIX)
    written = _write_partial(source, listed, partial)
    size = partial.stat().st_size
    if size != listed.size or written != listed.sha256:
        raise TickAdoptionRefused(
            f"{listed.name}: {size} bytes, SHA-256 {written}; {MANIFEST_NAME} "
            f"lists {listed.size} bytes, {listed.sha256} ({partial} kept)"
        )
    try:
        partial.rename(final)
    except OSError as exc:
        raise TickArchiveWriteError(final, exc) from exc
    return ArchivedFile(listed.name, listed.size, listed.sha256, copied=True)


def _require_space(
    job_dir: Path, archive_root: Path, files: list[ListedFile], free: FreeBytes
) -> None:
    needed = sum(f.size for f in files if not (job_dir / f.name).exists())
    available = free(archive_root)
    if needed > available:
        raise TickAdoptionRefused(
            f"archive volume at {archive_root} has {available} bytes free; "
            f"the job needs {needed} ({needed - available} short)"
        )


def archive_job_files(
    source_path: Path,
    job_id: str,
    archive_root: Path,
    free: FreeBytes = free_bytes,
) -> list[ArchivedFile]:
    """Copy and verify every file of the job into ``<archive_root>/<job_id>/``."""
    source = _Source(source_path)
    try:
        manifest_bytes, listed = _read_manifest(source, job_id)
        manifest = ListedFile(
            MANIFEST_NAME,
            len(manifest_bytes),
            hashlib.sha256(manifest_bytes).hexdigest(),
        )
        job_dir = archive_root / job_id
        _require_space(job_dir, archive_root, [*listed, manifest], free)
        try:
            job_dir.mkdir(exist_ok=True)
        except OSError as exc:
            raise TickArchiveWriteError(job_dir, exc) from exc
        return [_copy(source, item, job_dir) for item in [*listed, manifest]]
    finally:
        source.close()
