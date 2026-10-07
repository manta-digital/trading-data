"""The slice 228 drill's scratch server: config, start, wait, recovery target.

The server restores ``trading_tick`` from a base backup plus the tick WAL
archive (TD1 step 4). Before it starts, ``postgresql.auto.conf`` (copied from
production by the base backup, with ``archive_mode=on`` and the live
``archive_command``) is emptied and checked: a scratch server must never
archive into production's WAL directory. It listens on a socket in the drill
directory only.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from drill_228_lifecycle import RECOVERY_TIMEOUT, DrillError, Runner, pg_ctl
from wal_segment_name import parse_segment

AUTO_CONF = "postgresql.auto.conf"
#: Settings archive recovery requires at or above the primary's values
#: (PostgreSQL's hot-standby parameter check). Read from production in step 1,
#: never typed: emptying ``postgresql.auto.conf`` drops production's values.
PRIMARY_SETTINGS: tuple[str, ...] = (
    "max_connections",
    "max_worker_processes",
    "max_locks_per_transaction",
    "max_wal_senders",
    "max_prepared_transactions",
)
_POLL_SECONDS = 1.0
_LOG_TAIL_LINES = 20
_RESTORED = re.compile(
    r"restored\s+log\s+file\s+[\"']?([0-9A-F]{24})[\"']?\s+from\s+archive",
    re.IGNORECASE,
)


def restore_command(wal_dir: Path) -> str:
    """Runbook 200's two-shape command: a ``.zst`` segment, else a raw one."""
    wal = str(wal_dir)
    return f"test -f {wal}/%f.zst && zstd -dq {wal}/%f.zst -o %p || cp {wal}/%f %p"


def assert_no_archive_setting(datadir: Path) -> None:
    """Runbook 200 Step 6's ``grep -c archive`` = 0, on the auto.conf."""
    left = [
        ln for ln in (datadir / AUTO_CONF).read_text().splitlines() if "archive" in ln
    ]
    if left:
        raise DrillError(
            f"{AUTO_CONF} still holds archive settings: {left}; not starting"
        )


def _truncate(path: Path) -> None:
    path.write_text("")


def empty_auto_conf(datadir: Path) -> None:
    """Truncate production's ``postgresql.auto.conf`` and prove it is empty of
    archive settings. Any failure raises, and the server is never started."""
    path = datadir / AUTO_CONF
    if not path.is_file():
        raise DrillError(f"{path} is missing from the base backup")
    _truncate(path)
    assert_no_archive_setting(datadir)


def scratch_conf_text(sockdir: Path, prod: Mapping[str, str], wal_dir: Path) -> str:
    missing = [s for s in PRIMARY_SETTINGS if not prod.get(s)]
    if missing:
        raise DrillError(f"production settings not read: {missing}")
    lines = [
        "listen_addresses = ''",
        f"unix_socket_directories = '{sockdir}'",
        "archive_mode = off",
        "shared_preload_libraries = 'timescaledb'",
        "timescaledb.max_background_workers = 0",
        "timescaledb.telemetry_level = off",
        *(f"{name} = {prod[name]}" for name in PRIMARY_SETTINGS),
        f"restore_command = '{restore_command(wal_dir)}'",
    ]
    return "\n".join(lines) + "\n"


def write_scratch_conf(
    datadir: Path, sockdir: Path, prod: Mapping[str, str], wal_dir: Path
) -> None:
    """The scratch server's own config (the Debian layout keeps production's
    under /etc, so the base backup has none) and ``recovery.signal``."""
    text = scratch_conf_text(sockdir, prod, wal_dir)
    sockdir.mkdir(mode=0o700, exist_ok=True)
    sockdir.chmod(0o700)
    (datadir / "postgresql.conf").write_text(text)
    (datadir / "pg_hba.conf").write_text("local all all trust\n")
    (datadir / "pg_ident.conf").write_text("")
    (datadir / "recovery.signal").write_text("")


def log_tail(logfile: Path) -> str:
    lines = logfile.read_text().splitlines() if logfile.exists() else []
    return "\n".join(lines[-_LOG_TAIL_LINES:]) or "(log empty)"


def start_scratch(
    datadir: Path,
    logfile: Path,
    run: Runner,
    in_recovery: Callable[[], bool | None],
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> float:
    """Start the server and wait for recovery to end; seconds taken.

    ``pg_ctl start -w`` returns once the server accepts connections (a hot
    standby does when consistent) or reports its death, so the status checks
    below never race the postmaster writing its pid file. ``in_recovery``
    returns ``None`` while the server can't be reached. A server that exits
    during the wait fails with its log tail; one still in recovery past
    ``RECOVERY_TIMEOUT`` fails by name.
    """
    begin = clock()
    bound = str(int(RECOVERY_TIMEOUT.total_seconds()))
    started = run(
        pg_ctl("start", "-D", str(datadir), "-l", str(logfile), "-w", "-t", bound)
    )
    if started.returncode != 0:
        raise DrillError(
            f"pg_ctl start exited {started.returncode}:\n{log_tail(logfile)}"
        )
    while clock() - begin < RECOVERY_TIMEOUT.total_seconds():
        if run(pg_ctl("status", "-D", str(datadir))).returncode != 0:
            raise DrillError(
                f"scratch server exited during recovery:\n{log_tail(logfile)}"
            )
        if in_recovery() is False:
            return clock() - begin
        sleep(_POLL_SECONDS)
    raise DrillError(
        f"scratch server still in recovery after {RECOVERY_TIMEOUT}:\n"
        f"{log_tail(logfile)}"
    )


def last_restored_segment(log_text: str) -> str | None:
    """The last ``restored log file "<segment>" from archive`` in a server log."""
    found = _RESTORED.findall(log_text)
    return found[-1] if found else None


def check_reached(seen: str | None, needed: str) -> None:
    """Recovery must have restored step 2's segment or a later one."""
    if seen is None or parse_segment(seen) < parse_segment(needed):
        raise DrillError(
            f"recovery stopped early at {seen or 'no segment'} (needed {needed})"
        )
