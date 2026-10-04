"""Read ``deploy/backup-clusters.conf`` (slice 227, TD2).

The Python twin of ``deploy/lib/backup_clusters.sh``: same columns, same
rules, same errors. The cutover (227) and the restore drill (228) read
cluster paths from here so no tick path is a literal in a script. A parity
test holds the two parsers to the same field values on the real file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The checked-in table, relative to the checkout root.
TABLE_PATH = Path(__file__).resolve().parents[1] / "deploy" / "backup-clusters.conf"

#: ``-`` in an optional column: no sub-prefix / no host rewrite.
_NONE = "-"
_CRON_FIELDS = 5
_TOKENS_PER_ROW = 5 + 2 * _CRON_FIELDS
_URL_KEY_RE = re.compile(r"^MT_[A-Z0-9_]+$")
_ROOT_PREFIX = "/data/"


@dataclass(frozen=True)
class BackupCluster:
    cluster: str
    url_key: str
    backup_root: Path
    remote_subpath: str | None
    replication_host: str | None
    metadata_cron: str
    weekly_base_cron: str


def _optional(token: str) -> str | None:
    return None if token == _NONE else token


def _row_error(f: list[str], rows: list[BackupCluster]) -> str | None:
    """Why the tokens ``f`` are not a valid row, or ``None`` when they are."""
    if len(f) != _TOKENS_PER_ROW:
        return f"expected {_TOKENS_PER_ROW} fields, got {len(f)}"
    if any(r.cluster == f[0] for r in rows):
        return f"duplicate cluster {f[0]}"
    if not _URL_KEY_RE.match(f[1]):
        return f"url_key {f[1]} does not match {_URL_KEY_RE.pattern}"
    if not (f[2].startswith(_ROOT_PREFIX) and len(f[2]) > len(_ROOT_PREFIX)):
        return f"backup_root {f[2]} is not an absolute path under {_ROOT_PREFIX}"
    return None


def load_clusters(path: Path = TABLE_PATH) -> list[BackupCluster]:
    """Every row of the table, in file order; ``ValueError`` names the bad line."""
    rows: list[BackupCluster] = []
    for n, raw in enumerate(path.read_text().splitlines(), start=1):
        f = raw.split("#", 1)[0].split()
        if not f:
            continue
        if (error := _row_error(f, rows)) is not None:
            raise ValueError(f"{path} line {n}: {error}")
        rows.append(
            BackupCluster(
                cluster=f[0],
                url_key=f[1],
                backup_root=Path(f[2]),
                remote_subpath=_optional(f[3]),
                replication_host=_optional(f[4]),
                metadata_cron=" ".join(f[5 : 5 + _CRON_FIELDS]),
                weekly_base_cron=" ".join(f[5 + _CRON_FIELDS :]),
            )
        )
    if not rows:
        raise ValueError(f"{path} holds no rows")
    return rows


def cluster(path: Path, name: str) -> BackupCluster:
    """The row for ``name``; ``KeyError`` if the table has none."""
    for row in load_clusters(path):
        if row.cluster == name:
            return row
    raise KeyError(f"{path} has no cluster {name}")
