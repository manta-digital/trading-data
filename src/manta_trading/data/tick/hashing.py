"""SHA-256 of an archive file, read in bounded chunks (220, 223).

One implementation for every place a tick file's hash is computed: the
verified download, adoption's copy, and verification's re-hash.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Bytes read per ``update``: bounds memory on multi-hundred-megabyte files.
HASH_CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Lower-case hex SHA-256 of ``path``'s bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
