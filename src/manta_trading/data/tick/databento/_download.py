"""Verified batch-file download (design Technical Decision 9). No ``databento``.

Replaces the SDK's ``batch.download``, which has no request timeout and only
warns on a checksum mismatch. Each file is written to ``<name>.partial`` and
renamed to ``<name>`` only after its size and SHA-256 match what
``batch.list_files`` reported, so a final name is always verified and a
``.partial`` never is. There is no retry loop: a failure leaves what can be
resumed, and the caller's reconcile phase is the retry.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

import httpx

from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderPermanentError,
    ProviderTransientError,
)

PARTIAL_SUFFIX = ".partial"
_HASH_CHUNK_BYTES = 1024 * 1024
_AUTH_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})
#: The job's files are gone (expired or never existed): re-download is futile.
_GONE_STATUSES = frozenset({HTTPStatus.NOT_FOUND, HTTPStatus.GONE})


@dataclass(frozen=True)
class BatchFile:
    """One file of a batch job, as ``batch.list_files`` lists it."""

    filename: str
    size: int
    sha256: str
    url: str


def partial_path(final: Path) -> Path:
    return final.with_name(final.name + PARTIAL_SUFFIX)


def download_file(http: httpx.Client, file: BatchFile, dest_dir: Path) -> Path:
    """Fetch, resume, or just verify one file; return its verified final path."""
    final = dest_dir / file.filename
    if final.exists():
        _require_verified(final, file)
        return final
    partial = partial_path(final)
    have = partial.stat().st_size if partial.exists() else 0
    if have > file.size:
        partial.unlink()
        have = 0
    if have < file.size:
        _fetch(http, file, partial, have)
    _verify(partial, file)
    partial.rename(final)
    return final


def _fetch(http: httpx.Client, file: BatchFile, partial: Path, offset: int) -> None:
    """Stream the file (from ``offset`` when resuming) into ``partial``."""
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    try:
        with http.stream("GET", file.url, headers=headers) as response:
            mode = _write_mode(response.status_code, offset, file, partial)
            with partial.open(mode) as out:
                for chunk in response.iter_bytes():
                    out.write(chunk)
    except httpx.TransportError as exc:
        # Timeout, stall, or dropped connection: the .partial is kept to resume.
        raise ProviderTransientError(f"download {file.filename}: {exc!r}") from exc


def _write_mode(status: int, offset: int, file: BatchFile, partial: Path) -> str:
    """Append on 206-to-a-range; restart on 200; otherwise raise the mapped error."""
    if status == HTTPStatus.PARTIAL_CONTENT and offset:
        return "ab"
    if status == HTTPStatus.OK:
        return "wb"  # a 200 to a ranged request: the server ignored the range
    if status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
        partial.unlink(missing_ok=True)
        raise ProviderTransientError(
            f"download {file.filename}: HTTP 416; .partial deleted, restart"
        )
    raise _status_error(status, file)


def _status_error(status: int, file: BatchFile) -> ProviderError:
    detail = f"download {file.filename}: HTTP {status}"
    if status in _GONE_STATUSES:
        return ProviderPermanentError(f"{detail} (expired or missing)")
    if status in _AUTH_STATUSES:
        return ProviderAuthError(detail)
    if status == HTTPStatus.TOO_MANY_REQUESTS or status >= 500:
        return ProviderTransientError(detail)
    return ProviderPermanentError(detail)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(partial: Path, file: BatchFile) -> None:
    """Size and SHA-256 must match; a mismatch deletes the ``.partial``."""
    size = partial.stat().st_size
    if size != file.size:
        partial.unlink()
        raise ProviderTransientError(
            f"download {file.filename}: {size} bytes, expected {file.size}"
        )
    if _sha256(partial) != file.sha256:
        partial.unlink()
        raise ProviderTransientError(f"download {file.filename}: SHA-256 mismatch")


def _require_verified(final: Path, file: BatchFile) -> None:
    """A final name already present must be this very file."""
    if final.stat().st_size != file.size or _sha256(final) != file.sha256:
        raise ProviderPermanentError(
            f"{final} exists but does not match the listed file; refusing to touch it"
        )
