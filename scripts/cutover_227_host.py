"""Host primitives for the slice 227 cutover: the run context and the calls
that reach manta9000 (sudo, psql as postgres, cron job commands, setup-backup).
``cutover_227_tick_backup.py`` holds the steps; ``cutover_227_helpers.py`` the
pure text handling.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from backup_clusters import BackupCluster
from cutover_227_helpers import CRON_FILE, MAIN, TICK, Step, StepFailed, block_commands
from cutover_common import run

from manta_trading.data.tick.constants import (
    TICK_ACQUISITION_LOCK_KEY,
    TICK_INGEST_LOCK_KEY,
)

SCRIPTS = Path(__file__).resolve().parent
TICK_UNIT = "postgresql@17-tick"
#: setup-backup.sh's --backup-root: the host root (system/, restic files).
HOST_BACKUP_ROOT = Path("/data/backup")
RCLONE_REMOTE = "b2"
BUCKET_KEY = "MT_BACKUP_S3_BUCKET"
TICK_LOCK_KEYS = (TICK_ACQUISITION_LOCK_KEY, TICK_INGEST_LOCK_KEY)


@dataclass
class Context:
    checkout: Path
    main: BackupCluster
    tick: BackupCluster
    bucket_remote: str
    conf_sums: str = ""
    old_cron: str = ""
    lifecycle: list[str] = field(default_factory=list)

    @property
    def env_file(self) -> Path:
        return self.checkout / ".env"

    def tick_remote(self) -> str:
        sub = self.tick.remote_subpath
        return f"{self.bucket_remote}/{sub}" if sub else self.bucket_remote


# --- Host primitives ------------------------------------------------------------


def shell(command: str, step: Step) -> subprocess.CompletedProcess[str]:
    """Run a cron job's command as cron would (bash, as this user)."""
    step.seen.append(f"$ {command}")
    return subprocess.run(["bash", "-c", command], capture_output=True, text=True)


def psql(cluster_name: str, sql: str) -> str:
    """One value from a cluster, as postgres over the local socket."""
    args = ["-u", "postgres", "env", f"PGCLUSTER={cluster_name}", "psql", "-X", "-At"]
    return run([*args, "-d", "postgres", "-c", sql], sudo=True).stdout.strip()


def setup_backup(ctx: Context, *extra: str) -> subprocess.CompletedProcess[str]:
    args = [str(ctx.checkout / "deploy" / "setup-backup.sh"), "--checkout"]
    args += [str(ctx.checkout), "--env-file", str(ctx.env_file)]
    args += ["--backup-root", str(HOST_BACKUP_ROOT), *extra]
    return run(args, sudo=True, check=False)


def env_value(env_file: Path, key: str) -> str:
    """The one env-file read the backup tier uses (deploy/lib/env_value.sh)."""
    lib = SCRIPTS.parent / "deploy" / "lib" / "env_value.sh"
    cmd = f'. "{lib}"; env_value "$1" "$2"'
    return run(["bash", "-c", cmd, "_", str(env_file), key]).stdout.strip()


def conf_sums() -> str:
    """SHA-256 of production's postgresql.conf and postgresql.auto.conf."""
    rows = run(["pg_lsclusters", "--no-header"]).stdout.splitlines()
    datadir = next(r.split()[5] for r in rows if r.split()[:2] == MAIN.split("/"))
    files = [
        f"/etc/postgresql/{MAIN}/postgresql.conf",
        f"{datadir}/postgresql.auto.conf",
    ]
    return run(["sha256sum", *files], sudo=True).stdout.strip()


def tick_locks_held() -> int:
    keys = ",".join(str(k) for k in TICK_LOCK_KEYS)
    sql = (
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
        f"AND classid = 0 AND objsubid = 1 AND objid IN ({keys})"
    )
    return int(psql(TICK, sql))


def tick_commands() -> dict[str, str]:
    return block_commands(CRON_FILE.read_text(), TICK)


def require(condition: bool, step: Step, message: str) -> None:
    if not condition:
        step.seen.append(f"FAILED: {message}")
        raise StepFailed(message)


def record(step: Step, result: subprocess.CompletedProcess[str]) -> None:
    step.seen += [
        *(result.stdout or "").splitlines(),
        *(result.stderr or "").splitlines(),
    ]
    step.seen.append(f"exit {result.returncode}")
