"""Proof harness plumbing: the proof URLs, the free-space floor, the report file.

The URLs come from the checkout's ``.env`` through ``python-dotenv``, never by
sourcing it in a shell (a generated password may hold ``$``). They are named
``MT_PROOF_226_*``, not ``MT_TICK_*``: the tick preflight refuses any
``MT_TICK_*`` key that is not a known setting.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from manta_trading.data.tick.constants import TICK_ARCHIVE_DIR_ENV

PROOF_DB_URL_ENV = "MT_PROOF_226_DB_URL"
PROOF_MAINTENANCE_URL_ENV = "MT_PROOF_226_MAINTENANCE_URL"
ENV_FILE = Path(".env")
NOTES_DIR = Path("project-documents/user/notes")
SLICE = "226-slice.proof-on-existing-data"

#: Rows in ``tick_trade`` after ingesting the archive held at slice start
#: (every current tier unit); the denominator of the mapping and size steps.
EXPECTED_ROWS = 27_691_412

#: The archive volume and the free space a data-loading step needs on it
#: (LLD 226 TD2 failure modes).
DATA_VOLUME = Path("/data")
DATA_FREE_FLOOR_BYTES = 50 * 1000**3


class ProofSetupError(RuntimeError):
    """The harness cannot start: missing configuration or too little space."""


@dataclass(frozen=True)
class ProofUrls:
    db_url: str
    maintenance_url: str


def load_proof_urls(env_file: Path = ENV_FILE) -> ProofUrls:
    """The proof database's two URLs from ``env_file``; missing keys raise."""
    values = dotenv_values(env_file)
    missing = [
        key
        for key in (PROOF_DB_URL_ENV, PROOF_MAINTENANCE_URL_ENV)
        if not values.get(key)
    ]
    if missing:
        raise ProofSetupError(
            f"{env_file} lacks {', '.join(missing)}; run "
            "scripts/provision_tick_cluster.sh first"
        )
    return ProofUrls(
        db_url=str(values[PROOF_DB_URL_ENV]),
        maintenance_url=str(values[PROOF_MAINTENANCE_URL_ENV]),
    )


def archive_root(env_file: Path = ENV_FILE) -> Path:
    """``MT_TICK_ARCHIVE_DIR`` from ``env_file``; unset or missing raises."""
    root = dotenv_values(env_file).get(TICK_ARCHIVE_DIR_ENV)
    if not root or not Path(root).is_dir():
        raise ProofSetupError(
            f"{TICK_ARCHIVE_DIR_ENV} in {env_file} is not a directory"
        )
    return Path(root)


def require_free_space(
    volume: Path = DATA_VOLUME, floor_bytes: int = DATA_FREE_FLOOR_BYTES
) -> int:
    """Raise when ``volume`` has less than ``floor_bytes`` free; return free."""
    free = shutil.disk_usage(volume).free
    if free < floor_bytes:
        raise ProofSetupError(
            f"{volume} has {free / 1000**3:.1f} GB free, below the "
            f"{floor_bytes / 1000**3:.0f} GB floor; refusing to load data"
        )
    return free


@dataclass
class Report:
    """One step's report: ``<notes>/<date>-226-proof-<step>.md``."""

    step: str
    title: str
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    lines: list[str] = field(default_factory=list)

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)

    def table(self, header: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
        self.lines.append("| " + " | ".join(header) + " |")
        self.lines.append("|" + "---|" * len(header))
        self.lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
        self.lines.append("")

    def path(self, notes_dir: Path = NOTES_DIR) -> Path:
        return notes_dir / f"{self.started:%Y-%m-%d}-226-proof-{self.step}.md"

    def write(self, notes_dir: Path = NOTES_DIR) -> Path:
        stamp = f"{self.started:%Y%m%d}"
        front = [
            "---",
            "docType: note",
            "project: trading-data",
            f"slice: {SLICE}",
            f"dateCreated: {stamp}",
            f"dateUpdated: {stamp}",
            "status: complete",
            "---",
            "",
            f"# {self.title}",
            "",
            f"Step `{self.step}`, started {self.started:%Y-%m-%d %H:%M:%S} UTC by "
            "`scripts/proof_226_tick.py`.",
            "",
        ]
        target = self.path(notes_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(front + self.lines) + "\n")
        return target
