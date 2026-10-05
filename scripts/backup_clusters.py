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
#: Every token reaches a root-installed cron line: names, paths and hosts
#: are held to this charset, the cron fields to cron's (review F007).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_CRON_FIELD_RE = re.compile(r"^[0-9*/,-]+$")


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


def _row_error(tokens: list[str], rows: list[BackupCluster]) -> str | None:
    """Why ``tokens`` are not a valid row, or ``None`` when they are."""
    if len(tokens) != _TOKENS_PER_ROW:
        return f"expected {_TOKENS_PER_ROW} fields, got {len(tokens)}"
    if bad := [t for t in tokens[:5] if not _TOKEN_RE.match(t)]:
        return f"field {bad[0]!r} has characters outside {_TOKEN_RE.pattern}"
    if bad := [t for t in tokens[5:] if not _CRON_FIELD_RE.match(t)]:
        return f"cron field {bad[0]!r} has characters outside {_CRON_FIELD_RE.pattern}"
    if any(r.cluster == tokens[0] for r in rows):
        return f"duplicate cluster {tokens[0]}"
    if not _URL_KEY_RE.match(tokens[1]):
        return f"url_key {tokens[1]} does not match {_URL_KEY_RE.pattern}"
    root = tokens[2]
    if not (root.startswith(_ROOT_PREFIX) and len(root) > len(_ROOT_PREFIX)):
        return f"backup_root {root} is not an absolute path under {_ROOT_PREFIX}"
    if ".." in root.split("/"):
        return f"backup_root {root} has a '..' segment"
    return None


def load_clusters(path: Path = TABLE_PATH) -> list[BackupCluster]:
    """Every row of the table, in file order; ``ValueError`` names the bad line."""
    rows: list[BackupCluster] = []
    for line_no, raw in enumerate(path.read_text().splitlines(), start=1):
        tokens = raw.split("#", 1)[0].split()
        if not tokens:
            continue
        if (error := _row_error(tokens, rows)) is not None:
            raise ValueError(f"{path} line {line_no}: {error}")
        rows.append(
            BackupCluster(
                cluster=tokens[0],
                url_key=tokens[1],
                backup_root=Path(tokens[2]),
                remote_subpath=_optional(tokens[3]),
                replication_host=_optional(tokens[4]),
                metadata_cron=" ".join(tokens[5 : 5 + _CRON_FIELDS]),
                weekly_base_cron=" ".join(tokens[5 + _CRON_FIELDS :]),
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
