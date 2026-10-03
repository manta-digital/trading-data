"""``drop-proof`` and ``archive-check`` (LLD 226 TD2; tasks 11.1, 11.3).

``drop-proof`` drops the disposable proof database. ``DROP DATABASE`` cannot
run inside the database it drops, so it connects to ``trading_tick`` with the
maintenance URL. Its guard differs from ``@destructive``'s: the target is the
constant ``TICK_PROOF_DB_NAME``, never a parameter or a value read from a
URL, and it refuses when ``MT_TICK_DB_URL`` names that same database.

``archive-check`` is read-only: for each job directory it compares every file
``manifest.json`` lists against the listed size and ``sha256:`` hash.
``sha256sum -c`` cannot parse that JSON, and ``adopt_files._read_manifest``
is private, so the manifest is parsed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from manta_trading.data.tick.constants import TICK_PROOF_DB_NAME
from manta_trading.data.tick.hashing import sha256_file
from proof_226.common import ProofSetupError, Report, archive_root
from proof_226.final import database_named, require_tick_database, tick_urls
from proof_226.guard import SyncConn

MANIFEST = "manifest.json"
HASH_PREFIX = "sha256:"


class ProofDropRefused(RuntimeError):
    """The proof database's name is also the production tick database's."""


def require_distinct(production_db: str) -> None:
    if production_db == TICK_PROOF_DB_NAME:
        raise ProofDropRefused(
            f"MT_TICK_DB_URL names {production_db!r}, the proof database's own "
            "name; refusing to drop it"
        )


def drop_proof(conn: SyncConn, production_db: str) -> None:
    """Drop ``TICK_PROOF_DB_NAME`` from a connection to ``production_db``."""
    require_distinct(production_db)
    require_tick_database(conn, production_db)
    conn.execute(
        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
            sql.Identifier(TICK_PROOF_DB_NAME)
        )
    )


def run_drop() -> Path:
    urls = tick_urls()
    production_db = database_named(urls.db_url)
    report = Report("drop-proof", "Proof: drop the proof database (slice 226)")
    with psycopg.connect(urls.maintenance_url, autocommit=True) as conn:
        drop_proof(conn, production_db)
        gone = conn.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)",
            (TICK_PROOF_DB_NAME,),
        ).fetchone()
    if not gone or not gone[0]:
        raise ProofSetupError(f"{TICK_PROOF_DB_NAME} still exists after the drop")
    report.add(
        f"Dropped `{TICK_PROOF_DB_NAME}` from `{production_db}`; confirmed "
        "absent from pg_database."
    )
    return report.write()


@dataclass(frozen=True)
class FileCheck:
    job_id: str
    name: str
    problem: str | None


def check_job(job_dir: Path) -> list[FileCheck]:
    """Every file ``manifest.json`` lists: present, listed size, listed hash."""
    manifest = json.loads((job_dir / MANIFEST).read_text())
    job_id = str(manifest["job_id"])
    checks = []
    for entry in manifest["files"]:
        name, path = str(entry["filename"]), job_dir / str(entry["filename"])
        want = str(entry["hash"]).removeprefix(HASH_PREFIX)
        if not path.is_file():
            problem: str | None = "missing"
        elif path.stat().st_size != int(entry["size"]):
            problem = f"size {path.stat().st_size}, listed {entry['size']}"
        elif sha256_file(path) != want:
            problem = "SHA-256 differs from the listed hash"
        else:
            problem = None
        checks.append(FileCheck(job_id, name, problem))
    return checks


def run_archive_check() -> Path:
    root = archive_root()
    report = Report("archive-check", "Proof: archive integrity (slice 226)")
    jobs = sorted(p for p in root.iterdir() if p.is_dir())
    checks = [c for job in jobs for c in check_job(job)]
    bad = [c for c in checks if c.problem]
    report.add(
        f"{len(jobs)} job directories, {len(checks)} listed files, "
        f"{len(bad)} problems.",
        "",
    )
    if bad:
        report.table(
            ("job", "file", "problem"), [(c.job_id, c.name, c.problem) for c in bad]
        )
        path = report.write()
        raise ProofSetupError(f"archive-check found {len(bad)} problems; {path}")
    report.add("**Every listed file matches its manifest size and SHA-256.**")
    return report.write()
