"""Tests for ``scripts/cron_system_backup.sh`` (slice 920, Task 6.3).

``restic`` is stubbed on ``PATH`` and records its argv and the environment
it received, so the tests assert the load-bearing property directly: the
secrets travel by environment only and never appear on a command line.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "cron_system_backup.sh"
_SECRETS = ("appkey-secret", "restic-pw-secret", "keyid-secret")


def _stub(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text("#!/usr/bin/env bash\n" + body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def sysbk(tmp_path: Path) -> dict[str, Path]:
    env = tmp_path / "env"
    env.write_text(
        'MT_BACKUP_S3_ENDPOINT="https://s3.example.invalid"\n'
        'MT_BACKUP_S3_KEY_ID="keyid-secret"\n'
        'MT_BACKUP_S3_APPLICATION_KEY="appkey-secret"\n'
        'MT_BACKUP_S3_BUCKET="bucket-x"\n'
        'MT_BACKUP_RESTIC_PASSWORD="restic-pw-secret"\n'
    )
    excludes = tmp_path / "excludes"
    excludes.write_text("/home/manta/.cache\n")
    argv_log = tmp_path / "restic-argv"
    env_log = tmp_path / "restic-env"
    fail_on = tmp_path / "fail-on"  # contains a subcommand name to fail
    _stub(
        tmp_path / "bin",
        "restic",
        f'echo "$*" >> {str(argv_log)!r}\n'
        f'echo "RESTIC_REPOSITORY=$RESTIC_REPOSITORY RESTIC_PASSWORD=$RESTIC_PASSWORD '
        f"AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID "
        f'AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY" >> {str(env_log)!r}\n'
        f'[ -e {str(fail_on)!r} ] && [ "$(cat {str(fail_on)!r})" = "$1" ] && exit 1\n'
        f'[ "$1" = cat ] && exit 0\n'
        f"exit 0\n",
    )
    _stub(tmp_path / "bin", "logger", f'echo "$*" >> {str(tmp_path / "journal")!r}\n')
    return {
        "env": env,
        "excludes": excludes,
        "argv": argv_log,
        "envlog": env_log,
        "fail_on": fail_on,
        "stamp": tmp_path / "stamp",
        "log": tmp_path / "log",
        "lock": tmp_path / "lock",
        "journal": tmp_path / "journal",
        "bin": tmp_path / "bin",
    }


def _run(s: dict[str, Path], *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PATH=f"{s['bin']}:{os.environ['PATH']}")
    return subprocess.run(
        [str(_SCRIPT), *args], capture_output=True, text=True, timeout=60, env=env
    )


def _backup(s: dict[str, Path], *extra: str) -> subprocess.CompletedProcess[str]:
    return _run(
        s,
        "--env-file", str(s["env"]),
        "--repo-prefix", "system",
        "--exclude-file", str(s["excludes"]),
        "--stamp", str(s["stamp"]),
        "--log", str(s["log"]),
        "--lock", str(s["lock"]),
        *extra,
    )  # fmt: skip


def _argv(s: dict[str, Path]) -> list[str]:
    return s["argv"].read_text().splitlines() if s["argv"].exists() else []


class TestArguments:
    @pytest.mark.parametrize(
        "missing",
        ["--env-file", "--repo-prefix", "--exclude-file", "--stamp", "--log", "--lock"],
    )
    def test_each_required_argument_named(
        self, sysbk: dict[str, Path], missing: str
    ) -> None:
        values = {
            "--env-file": str(sysbk["env"]),
            "--repo-prefix": "system",
            "--exclude-file": str(sysbk["excludes"]),
            "--stamp": str(sysbk["stamp"]),
            "--log": str(sysbk["log"]),
            "--lock": str(sysbk["lock"]),
        }
        args = [a for k, v in values.items() if k != missing for a in (k, v)]
        result = _run(sysbk, *args)
        assert result.returncode == 2
        assert missing in result.stderr

    def test_check_mode_needs_no_exclude_file_or_stamp(
        self, sysbk: dict[str, Path]
    ) -> None:
        result = _run(
            sysbk,
            "--check",
            "--env-file", str(sysbk["env"]),
            "--repo-prefix", "system",
            "--log", str(sysbk["log"]),
            "--lock", str(sysbk["lock"]),
        )  # fmt: skip
        assert result.returncode == 0, result.stdout + result.stderr
        assert _argv(sysbk)[-1] == "check --read-data-subset=5%"
        assert not sysbk["stamp"].exists()

    def test_unknown_argument_refused(self, sysbk: dict[str, Path]) -> None:
        assert _backup(sysbk, "--frobnicate").returncode == 2


class TestBackup:
    def test_success_runs_unlock_backup_forget_and_touches_stamp(
        self, sysbk: dict[str, Path]
    ) -> None:
        result = _backup(sysbk)
        assert result.returncode == 0, result.stdout + result.stderr
        argv = _argv(sysbk)
        assert argv[0] == "cat config"  # the password/env precheck
        assert argv[1] == "unlock"
        assert argv[2] == (
            f"backup --one-file-system --exclude-file {sysbk['excludes']} "
            "/etc /root /var/spool/cron/crontabs /home/manta"
        )
        assert (
            argv[3] == "forget --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --prune"
        )
        assert sysbk["stamp"].exists()
        assert "system backup OK" in sysbk["log"].read_text()

    def test_secrets_travel_by_environment_only(self, sysbk: dict[str, Path]) -> None:
        _backup(sysbk)
        argv_text = sysbk["argv"].read_text()
        for secret in _SECRETS:
            assert secret not in argv_text
        env_line = sysbk["envlog"].read_text().splitlines()[-1]
        assert (
            "RESTIC_REPOSITORY=s3:https://s3.example.invalid/bucket-x/system"
            in env_line
        )
        assert "RESTIC_PASSWORD=restic-pw-secret" in env_line
        assert "AWS_ACCESS_KEY_ID=keyid-secret" in env_line
        assert "AWS_SECRET_ACCESS_KEY=appkey-secret" in env_line

    def test_missing_password_exits_without_running_restic(
        self, sysbk: dict[str, Path]
    ) -> None:
        sysbk["env"].write_text(
            "\n".join(
                line for line in sysbk["env"].read_text().splitlines()
                if "RESTIC_PASSWORD" not in line
            )
            + "\n"
        )  # fmt: skip
        result = _backup(sysbk)
        assert result.returncode == 1
        assert "MT_BACKUP_RESTIC_PASSWORD" in result.stderr
        assert _argv(sysbk) == []
        assert not sysbk["stamp"].exists()
        assert "MT_BACKUP_RESTIC_PASSWORD" in sysbk["log"].read_text()

    def test_backup_failure_leaves_stamp_and_logs(self, sysbk: dict[str, Path]) -> None:
        sysbk["fail_on"].write_text("backup")
        result = _backup(sysbk)
        assert result.returncode == 1
        assert not sysbk["stamp"].exists()
        assert "restic backup exited 1" in sysbk["log"].read_text()
        assert "restic backup exited 1" in sysbk["journal"].read_text()
        assert not any(a.startswith("forget") for a in _argv(sysbk))

    def test_forget_failure_leaves_stamp(self, sysbk: dict[str, Path]) -> None:
        sysbk["fail_on"].write_text("forget")
        result = _backup(sysbk)
        assert result.returncode == 1
        assert not sysbk["stamp"].exists()

    def test_lock_held_skips_with_exit_zero(self, sysbk: dict[str, Path]) -> None:
        with sysbk["lock"].open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            result = _backup(sysbk)
        assert result.returncode == 0
        assert "skipped: previous run active" in result.stdout
        assert _argv(sysbk) == ["cat config"]
        assert not sysbk["stamp"].exists()
