"""Host primitives of the restore drill (slice 228 TD1 steps 1-4).

Covers ``drill_228_host`` (sudo wrapper, backed-up set, restic listing,
archive-vs-snapshot) and ``drill_228_scratch`` (auto.conf guard, config,
start/wait, recovery target). The listing fixture is hand-written in
``restic ls --json`` form; real-format fixtures are added in task 6.8.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drill_228_host as host  # noqa: E402
import drill_228_scratch as scratch  # noqa: E402
from drill_228_lifecycle import RECOVERY_TIMEOUT, DrillError  # noqa: E402

ARCHIVE = Path("/data/tick-archive")
SNAPSHOT_TIME = "2026-10-07T02:00:05.123456789-06:00"
SNAPSHOT_EPOCH = datetime(2026, 10, 7, 8, 0, 5, tzinfo=UTC).timestamp()
PROD = {
    "max_connections": "100",
    "max_worker_processes": "27",
    "max_locks_per_transaction": "512",
    "max_wal_senders": "10",
    "max_prepared_transactions": "0",
}


def done(code: int = 0, err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, "", err)


# --- sudo -------------------------------------------------------------------------


def test_an_expired_sudo_timestamp_fails_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        host,
        "run",
        lambda args, timeout=None: done(1, "sudo: a password is required\n"),
    )
    with pytest.raises(host.SudoExpired, match="sudo timestamp expired"):
        host.sudo_n(["rm", "-rf", "/data/restore-test/228-drill-x"])


def test_sudo_n_prepends_sudo_dash_n(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def record(
        args: list[str], timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return done()

    monkeypatch.setattr(host, "run", record)
    host.sudo_n(["chown", "-R", "manta:manta", "/x"])
    assert seen == [["sudo", "-n", "chown", "-R", "manta:manta", "/x"]]


# --- The backed-up set ---------------------------------------------------------------


@pytest.fixture
def excludes() -> list:
    return host.load_excludes()


def test_the_real_exclude_file_drops_partial_tick_files(excludes: list) -> None:
    assert host.is_excluded(f"{ARCHIVE}/GLBX-1/glbx.dbn.zst.partial", excludes)
    assert not host.is_excluded(f"{ARCHIVE}/GLBX-1/glbx.dbn.zst", excludes)
    assert host.is_excluded("/home/manta/x/.venv/lib/a.py", excludes)


def _archive(tmp_path: Path, mtime: float = SNAPSHOT_EPOCH - 60) -> Path:
    root = tmp_path / "tick-archive"
    job = root / "GLBX-1"
    job.mkdir(parents=True)
    for name, body in [
        ("manifest.json", "{}"),
        ("a.dbn.zst", "abcd"),
        ("b.dbn.zst", "ef"),
    ]:
        (job / name).write_text(body)
        os.utime(job / name, (mtime, mtime))
    (job / "c.dbn.zst.partial").write_text("unfinished")
    return root


def test_backed_up_set_skips_excluded_files(tmp_path: Path, excludes: list) -> None:
    live = host.backed_up_set(_archive(tmp_path), excludes, logical_root=ARCHIVE)
    assert (live.count, live.bytes) == (3, 8)


def _listing(files: list[tuple[str, int]], *, noise: bool = False) -> str:
    snap = {
        "time": SNAPSHOT_TIME,
        "paths": ["/data/tick-archive"],
        "struct_type": "snapshot",
    }
    lines = [json.dumps(snap)]
    lines.append(
        json.dumps({"name": "GLBX-1", "type": "dir", "path": f"{ARCHIVE}/GLBX-1"})
    )
    for name, size in files:
        node = {"name": name, "type": "file", "path": f"{ARCHIVE}/GLBX-1/{name}"}
        lines.append(json.dumps(node | {"size": size, "struct_type": "node"}))
    if noise:
        lines = ["", "  " + lines[0] + "  ", "warning: something", *lines[1:], ""]
    return "\n".join(lines)


EQUAL = [("manifest.json", 2), ("a.dbn.zst", 4), ("b.dbn.zst", 2)]


@pytest.mark.parametrize("noise", [False, True])
def test_restic_listing_parses_leniently(noise: bool, excludes: list) -> None:
    listing = host.parse_restic_ls(_listing(EQUAL, noise=noise), excludes)
    assert (listing.files.count, listing.files.bytes) == (3, 8)
    assert listing.time == datetime(2026, 10, 7, 8, 0, 5, 123456, tzinfo=UTC)


def test_a_listing_without_a_snapshot_line_fails(excludes: list) -> None:
    with pytest.raises(DrillError, match="no snapshot"):
        host.parse_restic_ls(_listing(EQUAL).split("\n", 1)[1], excludes)


@pytest.mark.parametrize(
    ("files", "mtime", "problem"),
    [
        (EQUAL, SNAPSHOT_EPOCH - 60, None),
        (EQUAL[:2], SNAPSHOT_EPOCH - 60, "files: live 3, snapshot 2"),
        ([*EQUAL[:2], ("b.dbn.zst", 3)], SNAPSHOT_EPOCH - 60, "bytes: live 8"),
        (EQUAL, SNAPSHOT_EPOCH + 60, "newest live file"),
    ],
)
def test_archive_against_snapshot(
    tmp_path: Path, excludes: list, files: list, mtime: float, problem: str | None
) -> None:
    live = host.backed_up_set(_archive(tmp_path, mtime), excludes, logical_root=ARCHIVE)
    listing = host.parse_restic_ls(_listing(files), excludes)
    if problem is None:
        host.check_archive_against_snapshot(live, listing)
        return
    with pytest.raises(DrillError, match="archive changed since snapshot") as raised:
        host.check_archive_against_snapshot(live, listing)
    assert problem in str(raised.value)


# --- 5.6: auto.conf guard and config ----------------------------------------


def test_auto_conf_with_archive_command_is_truncated(tmp_path: Path) -> None:
    (tmp_path / scratch.AUTO_CONF).write_text(
        "archive_mode = 'on'\narchive_command = 'cp %p /data/backup/17-tick/wal/%f'\n"
    )
    scratch.empty_auto_conf(tmp_path)
    assert (tmp_path / scratch.AUTO_CONF).read_text() == ""


def test_an_archive_line_left_behind_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / scratch.AUTO_CONF).write_text("archive_mode = 'on'\n")
    monkeypatch.setattr(scratch, "_truncate", lambda path: None)
    with pytest.raises(DrillError, match="still holds archive settings"):
        scratch.empty_auto_conf(tmp_path)


def test_a_missing_auto_conf_refuses(tmp_path: Path) -> None:
    with pytest.raises(DrillError, match="missing"):
        scratch.empty_auto_conf(tmp_path)


def test_scratch_config_carries_every_required_line(tmp_path: Path) -> None:
    sock, wal = tmp_path / "sock", Path("/data/backup/17-tick/wal")
    scratch.write_scratch_conf(tmp_path, sock, PROD, wal)
    conf = (tmp_path / "postgresql.conf").read_text()
    for line in [
        "listen_addresses = ''",
        f"unix_socket_directories = '{sock}'",
        "archive_mode = off",
        "shared_preload_libraries = 'timescaledb'",
        "timescaledb.max_background_workers = 0",
        *(f"{k} = {v}" for k, v in PROD.items()),
    ]:
        assert line in conf.splitlines()
    assert (tmp_path / "pg_hba.conf").read_text() == "local all all trust\n"
    assert (tmp_path / "recovery.signal").exists()
    assert sock.stat().st_mode & 0o777 == 0o700


def test_restore_command_handles_zst_and_raw() -> None:
    cmd = scratch.restore_command(Path("/w"))
    assert cmd == "test -f /w/%f.zst && zstd -dq /w/%f.zst -o %p || cp /w/%f %p"


@pytest.mark.parametrize("missing", scratch.PRIMARY_SETTINGS)
def test_a_missing_production_setting_raises(tmp_path: Path, missing: str) -> None:
    prod = {k: v for k, v in PROD.items() if k != missing}
    with pytest.raises(DrillError, match=missing):
        scratch.write_scratch_conf(tmp_path, tmp_path / "sock", prod, Path("/w"))


# --- 5.8: start and wait --------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _pg_ctl(status_codes: list[int]):
    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return done(status_codes.pop(0) if args[1] == "status" else 0)

    return run


def test_recovery_finishing_returns(tmp_path: Path) -> None:
    clock, answers = Clock(), [None, True, False]
    took = scratch.start_scratch(
        tmp_path,
        tmp_path / "log",
        _pg_ctl([0, 0, 0]),
        lambda: answers.pop(0),
        clock=clock,
        sleep=clock.sleep,
    )
    assert took == 2.0


def test_a_server_exiting_during_the_wait_fails_with_the_log_tail(
    tmp_path: Path,
) -> None:
    (tmp_path / "log").write_text(
        "LOG: starting\nFATAL: requested timeline 2 not found\n"
    )
    clock = Clock()
    with pytest.raises(DrillError, match="exited during recovery") as raised:
        scratch.start_scratch(
            tmp_path,
            tmp_path / "log",
            _pg_ctl([0, 3]),
            lambda: True,
            clock=clock,
            sleep=clock.sleep,
        )
    assert "requested timeline 2 not found" in str(raised.value)


def test_a_wait_past_the_bound_fails_by_name(tmp_path: Path) -> None:
    clock = Clock()
    forever = [0] * (int(RECOVERY_TIMEOUT.total_seconds()) + 2)
    with pytest.raises(DrillError, match="still in recovery after"):
        scratch.start_scratch(
            tmp_path,
            tmp_path / "log",
            _pg_ctl(forever),
            lambda: True,
            clock=clock,
            sleep=clock.sleep,
        )


# --- 5.10: recovery target ------------------------------------------------------------

LOG = """07:00:01.000 MDT [1] LOG:  starting archive recovery
07:00:01.100 MDT [1] LOG:  restored log file "00000001000000290000007A" from archive
07:00:01.200 MDT [1] LOG:  restored  log file  '00000001000000290000007B'   from archive
07:00:01.250 MDT [1] LOG:  restored log file "00000002.history" from archive
07:00:01.300 MDT [1] LOG:  restored log file 00000001000000290000007C from archive
07:00:01.400 MDT [1] LOG:  archive recovery complete
"""


def test_the_log_parser_reads_the_last_restored_segment() -> None:
    assert scratch.last_restored_segment(LOG) == "00000001000000290000007C"
    assert scratch.last_restored_segment("LOG: starting\n") is None


@pytest.mark.parametrize(
    ("seen", "needed", "ok"),
    [
        ("00000001000000290000007C", "00000001000000290000007C", True),
        ("00000001000000290000007D", "00000001000000290000007C", True),
        ("000000010000002A00000000", "0000000100000029000000FF", True),
        ("00000001000000290000007B", "00000001000000290000007C", False),
        (None, "00000001000000290000007C", False),
    ],
)
def test_check_reached(seen: str | None, needed: str, ok: bool) -> None:
    if ok:
        scratch.check_reached(seen, needed)
        return
    with pytest.raises(DrillError, match="recovery stopped early at"):
        scratch.check_reached(seen, needed)


def test_start_waits_for_the_server_and_a_failed_start_shows_the_log(
    tmp_path: Path,
) -> None:
    (tmp_path / "log").write_text("FATAL: could not load timescaledb\n")
    seen: list[list[str]] = []

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return done(1)

    with pytest.raises(DrillError, match="could not load timescaledb"):
        scratch.start_scratch(tmp_path, tmp_path / "log", run, lambda: False)
    assert seen[0][1] == "start" and "-w" in seen[0]
    assert seen[0][seen[0].index("-t") + 1] == str(
        int(RECOVERY_TIMEOUT.total_seconds())
    )
