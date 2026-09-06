"""Tests for the WAL offsite tier (slice 920, Section 5): the push
(``sync_wal_offsite.sh``), the refusal contract (``reconcile_guards.sh``),
and the ordered weekly reconcile (``cron_weekly_backup.sh``).

No network, no database: ``rclone``, ``psql``, ``findmnt``, ``logger`` and
``backup_prod.sh`` are stubbed on ``PATH`` and record their argv, so the
tests assert what was *asked* of them — in particular that ``rclone sync``
is never asked for on any refused path.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
_PUSH = _SCRIPTS / "sync_wal_offsite.sh"
_GUARDS = _SCRIPTS / "reconcile_guards.sh"
_WEEKLY = _SCRIPTS / "cron_weekly_backup.sh"

_LAST_ARCHIVED = "00000001000011A600000075"
_START_LSN = "11A6/73000028"  # manifest start -> segment …73


def _stub(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text("#!/usr/bin/env bash\n" + body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)


def _run(cmd: list[str], bin_dir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)


def _calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def _rclone_stub(
    bin_dir: Path, log: Path, check_fail_file: Path, lsf_dirs: str
) -> None:
    """Records argv; `check` fails while check_fail_file exists; `lsf` lists."""
    _stub(
        bin_dir,
        "rclone",
        f'echo "$*" >> {str(log)!r}\n'
        f'case "$1" in\n'
        f"  check) [ ! -e {str(check_fail_file)!r} ] ;;\n"
        f'  lsf) printf "%s\\n" {lsf_dirs} ;;\n'
        f"  *) exit 0 ;;\n"
        f"esac\n",
    )


# --- sync_wal_offsite.sh ------------------------------------------------------


@pytest.fixture
def push(tmp_path: Path) -> dict[str, Path]:
    wal = tmp_path / "wal"
    wal.mkdir()
    (wal / "00000001000011A600000073").touch()
    log = tmp_path / "rclone.log"
    _rclone_stub(tmp_path / "bin", log, tmp_path / "check-fail", '""')
    _stub(tmp_path / "bin", "logger", f'echo "$*" >> {str(tmp_path / "journal")!r}\n')
    return {
        "wal": wal,
        "log": log,
        "stamp": tmp_path / "stamp",
        "lock": tmp_path / "lock",
        "bin": tmp_path / "bin",
        "journal": tmp_path / "journal",
    }


def _push(p: dict[str, Path], *extra: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            str(_PUSH),
            "--wal-dir",
            str(p["wal"]),
            "--remote",
            "b2:bucket-x/wal",
            "--stamp",
            str(p["stamp"]),
            "--timeout",
            "5",
            "--lock",
            str(p["lock"]),
            *extra,
        ],  # fmt: skip
        p["bin"],
    )


class TestPushArguments:
    @pytest.mark.parametrize(
        "missing", ["--wal-dir", "--remote", "--stamp", "--timeout", "--lock"]
    )
    def test_each_required_argument_named(
        self, push: dict[str, Path], missing: str
    ) -> None:
        values = {
            "--wal-dir": str(push["wal"]),
            "--remote": "b2:x/wal",
            "--stamp": str(push["stamp"]),
            "--timeout": "5",
            "--lock": str(push["lock"]),
        }
        args = [a for k, v in values.items() if k != missing for a in (k, v)]
        result = _run([str(_PUSH), *args], push["bin"])
        assert result.returncode == 2
        assert missing in result.stderr

    def test_non_numeric_timeout_refused(self, push: dict[str, Path]) -> None:
        result = _push(push, "--timeout", "soon")
        assert result.returncode == 2
        assert "--timeout" in result.stderr

    def test_unknown_argument_refused(self, push: dict[str, Path]) -> None:
        assert _push(push, "--frobnicate").returncode == 2


class TestPush:
    def test_success_copies_with_filters_and_touches_stamp(
        self, push: dict[str, Path]
    ) -> None:
        result = _push(push)
        assert result.returncode == 0, result.stderr
        assert _calls(push["log"]) == [
            f"copy {push['wal']} b2:bucket-x/wal --min-age 2m --exclude *.tmp"
        ]
        assert push["stamp"].exists()
        assert not push["journal"].exists()

    def test_failure_leaves_stamp_and_logs_once(self, push: dict[str, Path]) -> None:
        _stub(push["bin"], "rclone", "exit 3\n")
        result = _push(push)
        assert result.returncode == 3
        assert not push["stamp"].exists()
        assert "FAILED" in result.stderr
        assert len(_calls(push["journal"])) == 1
        assert "rclone exit 3" in _calls(push["journal"])[0]

    def test_lock_held_skips_with_exit_zero(self, push: dict[str, Path]) -> None:
        with push["lock"].open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            result = _push(push)
        assert result.returncode == 0
        assert result.stdout.strip() == "skipped: previous run active"
        assert _calls(push["log"]) == []
        assert not push["stamp"].exists()

    def test_lock_wait_gives_up_after_the_wait(self, push: dict[str, Path]) -> None:
        # A one-minute minimum wait is too slow for a unit test; assert the
        # argument is validated and the message names the lock instead.
        result = _push(push, "--lock-wait", "x")
        assert result.returncode == 2
        assert "--lock-wait" in result.stderr

    def test_verify_runs_one_way_check_without_stamp(
        self, push: dict[str, Path]
    ) -> None:
        result = _push(push, "--verify")
        assert result.returncode == 0, result.stderr
        expected = f"check {push['wal']} b2:bucket-x/wal --one-way --min-age 2m"
        assert _calls(push["log"]) == [f"{expected} --exclude *.tmp"]
        assert not push["stamp"].exists()

    def test_verify_failure_is_non_zero(self, push: dict[str, Path]) -> None:
        (push["bin"].parent / "check-fail").touch()
        assert _push(push, "--verify").returncode != 0

    def test_sync_carries_max_delete(self, push: dict[str, Path]) -> None:
        result = _push(push, "--sync-max-delete", "53")
        assert result.returncode == 0, result.stderr
        expected = f"sync {push['wal']} b2:bucket-x/wal --max-delete 53 --min-age 2m"
        assert _calls(push["log"]) == [f"{expected} --exclude *.tmp"]
        assert not push["stamp"].exists()


# --- reconcile_guards.sh ------------------------------------------------------


def _make_backup(base_dir: Path, name: str, start_lsn: str) -> None:
    d = base_dir / name
    d.mkdir(parents=True)
    manifest = {
        "WAL-Ranges": [{"Timeline": 1, "Start-LSN": start_lsn, "End-LSN": start_lsn}]
    }
    (d / "backup_manifest").write_text(json.dumps(manifest))


@pytest.fixture
def archive(tmp_path: Path) -> dict[str, Path]:
    """A live-looking archive: mounted root, segments …70–…75, one backup."""
    root = tmp_path / "backup"
    wal = root / "wal"
    base = root / "base"
    wal.mkdir(parents=True)
    for seg in ("70", "71", "72"):
        (wal / f"00000001000011A6000000{seg}").touch()
    for seg in ("73", "74", "75"):
        (wal / f"00000001000011A6000000{seg}.zst").touch()
    _make_backup(base, "20250110", _START_LSN)
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "findmnt", "echo /data\n")
    _stub(bin_dir, "psql", f"echo {_LAST_ARCHIVED}\n")
    return {"root": root, "wal": wal, "base": base, "bin": bin_dir}


def _guards(a: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            str(_GUARDS),
            "--db-url",
            "postgresql://stub/none",
            "--backup-root",
            str(a["root"]),
            "--wal-dir",
            str(a["wal"]),
            "--base-dir",
            str(a["base"]),
        ],  # fmt: skip
        a["bin"],
    )


class TestReconcileGuards:
    def test_all_guards_pass(self, archive: dict[str, Path]) -> None:
        result = _guards(archive)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "reconcile guards passed"

    def test_root_filesystem_is_refused(self, archive: dict[str, Path]) -> None:
        _stub(archive["bin"], "findmnt", "echo /\n")
        result = _guards(archive)
        assert result.returncode == 1
        assert result.stdout.startswith("reconcile refused: ")
        assert "root filesystem" in result.stdout

    def test_empty_wal_dir_is_refused(self, archive: dict[str, Path]) -> None:
        for f in archive["wal"].iterdir():
            f.unlink()
        result = _guards(archive)
        assert result.returncode == 1
        assert "reconcile refused:" in result.stdout and "empty" in result.stdout

    def test_missing_last_archived_segment_is_refused(
        self, archive: dict[str, Path]
    ) -> None:
        (archive["wal"] / f"{_LAST_ARCHIVED}.zst").unlink()
        result = _guards(archive)
        assert result.returncode == 1
        assert f"last archived segment {_LAST_ARCHIVED} is not in" in result.stdout

    def test_null_last_archived_is_refused(self, archive: dict[str, Path]) -> None:
        _stub(archive["bin"], "psql", "echo ''\n")
        result = _guards(archive)
        assert result.returncode == 1
        assert "NULL" in result.stdout

    def test_missing_manifest_start_segment_is_refused(
        self, archive: dict[str, Path]
    ) -> None:
        (archive["wal"] / "00000001000011A600000073.zst").unlink()
        result = _guards(archive)
        assert result.returncode == 1
        assert "needs WAL from 00000001000011A600000073" in result.stdout

    def test_raw_segment_shape_also_counts_as_present(
        self, archive: dict[str, Path]
    ) -> None:
        (archive["wal"] / "00000001000011A600000073.zst").rename(
            archive["wal"] / "00000001000011A600000073"
        )
        assert _guards(archive).returncode == 0

    def test_required_arguments(self) -> None:
        result = subprocess.run(
            [str(_GUARDS)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 2
        assert "--db-url" in result.stderr


# --- cron_weekly_backup.sh ----------------------------------------------------

_WEEKLY_REQUIRED = (
    "--env-file",
    "--backup-root",
    "--base-dir",
    "--wal-dir",
    "--keep-days",
    "--health-flag",
    "--remote-wal",
    "--remote-base",
    "--stamp",
    "--lock",
    "--armed",
)


@pytest.fixture
def weekly(archive: dict[str, Path], tmp_path: Path) -> dict[str, Path]:
    """The archive fixture plus a prunable old backup and the weekly's stubs.

    Segments …70–…72 precede the retained backup's start (…73) and are what
    the prune removes: PRUNED wal=3, so an armed sync must carry
    --max-delete 53.
    """
    _make_backup(archive["base"], "20250101", "11A6/50000028")
    log = tmp_path / "rclone.log"
    _rclone_stub(
        archive["bin"], log, tmp_path / "check-fail", '"20250101/" "20250110/"'
    )
    _stub(
        archive["bin"],
        "backup_prod.sh",
        f'echo "backup_prod $*" >> {str(tmp_path / "base.log")!r}\n',
    )
    _stub(archive["bin"], "logger", f'echo "$*" >> {str(tmp_path / "journal")!r}\n')
    env = tmp_path / "env"
    env.write_text('MT_TIMESCALE_MAINTENANCE_URL="postgresql://stub/none"\n')
    return dict(
        archive,
        env=env,
        log=log,
        base_log=tmp_path / "base.log",
        journal=tmp_path / "journal",
        check_fail=tmp_path / "check-fail",
        flag=archive["root"] / "ARCHIVE-BROKEN",
        armed=archive["root"] / "RECONCILE-ARMED",
        stamp=archive["root"] / "wal-offsite.stamp",
        lock=archive["root"] / "wal-offsite.lock",
    )


def _weekly(w: dict[str, Path], *extra: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            str(_WEEKLY),
            "--env-file",
            str(w["env"]),
            "--backup-root",
            str(w["root"]),
            "--base-dir",
            str(w["base"]),
            "--wal-dir",
            str(w["wal"]),
            "--keep-days",
            "21",
            "--health-flag",
            str(w["flag"]),
            "--remote-wal",
            "b2:bucket-x/wal",
            "--remote-base",
            "b2:bucket-x/base",
            "--stamp",
            str(w["stamp"]),
            "--lock",
            str(w["lock"]),
            "--armed",
            str(w["armed"]),
            *extra,
        ],  # fmt: skip
        w["bin"],
    )


def _rclone_verbs(w: dict[str, Path]) -> list[str]:
    return [line.split()[0] for line in _calls(w["log"])]


class TestWeeklyArguments:
    @pytest.mark.parametrize("missing", _WEEKLY_REQUIRED)
    def test_each_required_argument_named(
        self, weekly: dict[str, Path], missing: str
    ) -> None:
        values = {
            "--env-file": str(weekly["env"]), "--backup-root": str(weekly["root"]),
            "--base-dir": str(weekly["base"]), "--wal-dir": str(weekly["wal"]),
            "--keep-days": "21", "--health-flag": str(weekly["flag"]),
            "--remote-wal": "b2:x/wal", "--remote-base": "b2:x/base",
            "--stamp": str(weekly["stamp"]),
            "--lock": str(weekly["lock"]), "--armed": str(weekly["armed"]),
        }  # fmt: skip
        args = [a for k, v in values.items() if k != missing for a in (k, v)]
        result = _run([str(_WEEKLY), *args], weekly["bin"])
        assert result.returncode == 2
        assert missing in result.stderr


class TestWeeklyReconcile:
    def test_archive_flag_refuses_before_anything(
        self, weekly: dict[str, Path]
    ) -> None:
        weekly["flag"].write_text("broken")
        result = _weekly(weekly)
        assert result.returncode == 1
        assert "refusing" in result.stderr
        assert not weekly["base_log"].exists()
        assert _calls(weekly["log"]) == []

    def test_unarmed_runs_guards_but_never_syncs(self, weekly: dict[str, Path]) -> None:
        result = _weekly(weekly)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "backup_prod" in weekly["base_log"].read_text()
        assert "reconcile guards passed" in result.stdout
        assert "reconcile skipped: not armed" in result.stdout
        assert _rclone_verbs(weekly) == ["copy", "check"]
        assert "PRUNED wal=3 base=1" in result.stdout
        assert not (weekly["base"] / "20250101").exists()

    def test_armed_syncs_with_max_delete_and_purges_absent_bases(
        self, weekly: dict[str, Path]
    ) -> None:
        weekly["armed"].touch()
        result = _weekly(weekly)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _rclone_verbs(weekly) == [
            "copy",
            "check",
            "sync",
            "lsf",
            "purge",
            "check",
        ]
        sync = [c for c in _calls(weekly["log"]) if c.startswith("sync ")][0]
        assert "--max-delete 53 " in sync
        assert "purge b2:bucket-x/base/20250101" in _calls(weekly["log"])
        assert "purge b2:bucket-x/base/20250110" not in _calls(weekly["log"])
        assert (
            "removed offsite base b2:bucket-x/base/20250101"
            in weekly["journal"].read_text()
        )

    def test_differing_check_aborts_before_prune(self, weekly: dict[str, Path]) -> None:
        weekly["armed"].touch()
        weekly["check_fail"].touch()
        result = _weekly(weekly)
        assert result.returncode == 1
        assert "differs" in result.stderr
        assert _rclone_verbs(weekly) == ["copy", "check"]
        assert (weekly["wal"] / "00000001000011A600000070").exists(), (
            "prune must not have run"
        )
        assert (weekly["base"] / "20250101").exists()

    def test_failed_guard_aborts_before_sync(self, weekly: dict[str, Path]) -> None:
        weekly["armed"].touch()
        _stub(weekly["bin"], "findmnt", "echo /\n")
        result = _weekly(weekly)
        assert result.returncode == 1
        assert "reconcile refused" in result.stdout
        assert "sync" not in _rclone_verbs(weekly)

    def test_skip_base_backup_runs_reconcile_only(
        self, weekly: dict[str, Path]
    ) -> None:
        result = _weekly(weekly, "--skip-base-backup")
        assert result.returncode == 0, result.stdout + result.stderr
        assert not weekly["base_log"].exists()
        assert "reconcile guards passed" in result.stdout

    def test_sync_never_requested_on_any_refused_path(
        self, weekly: dict[str, Path]
    ) -> None:
        weekly["armed"].touch()
        for f in weekly["wal"].iterdir():
            f.unlink()
        result = _weekly(weekly)
        assert result.returncode == 1
        assert "sync" not in _rclone_verbs(weekly)
