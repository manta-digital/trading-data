"""Tests for ``scripts/check_backup_health.sh`` (slice 920, Tasks 1.3a/1.5a/1.6a).

No database: ``check_archive_health.sh`` and ``psql`` are stubbed on ``PATH``
so each test chooses what the four database checks report and what
``pg_stat_archiver`` says about the last archived segment. Everything else
is a real ``tmp_path`` layout: an archive directory, a base-backup directory,
and the two success stamps.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "check_backup_health.sh"

_INNER_PASS = "PASS archive healthy (mode=on, unarchived_bytes=0)"
_LAST_ARCHIVED = "000000010000121700000083"
_NEXT_SEGMENT = "000000010000121700000084"
_WHOLE_SEGMENT = 16 * 1024 * 1024

_REQUIRED = (
    "--db-url",
    "--pgdata",
    "--wal-dir",
    "--stamp",
    "--stale-after",
    "--system-stamp",
    "--base-dir",
)


def _age(path: Path, minutes: float) -> None:
    then = time.time() - minutes * 60
    os.utime(path, (then, then))


def _write_stub(bin_dir: Path, name: str, stdout_lines: list[str], rc: int) -> None:
    body = "#!/usr/bin/env bash\n"
    for line in stdout_lines:
        body += f"echo {line!r}\n"
    body += f"exit {rc}\n"
    stub = bin_dir / name
    stub.write_text(body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)


@dataclass
class Layout:
    root: Path
    bin: Path
    wal: Path
    base: Path
    stamp: Path
    system_stamp: Path

    def stub_inner(self, lines: list[str], rc: int) -> None:
        _write_stub(self.bin, "check_archive_health.sh", lines, rc)

    def stub_psql(self, failing: str, last_archived: str, last_failed: str) -> None:
        _write_stub(self.bin, "psql", [f"{failing}|{last_archived}|{last_failed}"], 0)

    def run(self, *overrides: str) -> subprocess.CompletedProcess[str]:
        args = [
            "--db-url", "postgresql://stub/none",
            "--pgdata", str(self.root / "pgdata"),
            "--wal-dir", str(self.wal),
            "--stamp", str(self.stamp),
            "--stale-after", "180",
            "--system-stamp", str(self.system_stamp),
            "--base-dir", str(self.base),
            *overrides,
        ]  # fmt: skip
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")
        return subprocess.run(
            [str(_SCRIPT), *args], capture_output=True, text=True, timeout=60, env=env
        )


@pytest.fixture
def healthy(tmp_path: Path) -> Layout:
    """Everything green: inner PASS, fresh stamps, a base backup dated today."""
    layout = Layout(
        root=tmp_path,
        bin=tmp_path / "bin",
        wal=tmp_path / "wal",
        base=tmp_path / "base",
        stamp=tmp_path / "wal-offsite.stamp",
        system_stamp=tmp_path / "system-backup.stamp",
    )
    for d in (layout.bin, layout.wal, layout.base, tmp_path / "pgdata"):
        d.mkdir()
    (layout.base / date.today().strftime("%Y%m%d")).mkdir()
    layout.stamp.touch()
    layout.system_stamp.touch()
    layout.stub_inner([_INNER_PASS], 0)
    layout.stub_psql("f", _LAST_ARCHIVED, "")
    return layout


def _fail_names(output: str) -> list[str]:
    return [
        line.split()[1].rstrip(":")
        for line in output.splitlines()
        if line.startswith("FAIL ")
    ]


def _flags_line(output: str) -> str:
    return output.strip().splitlines()[-1]


class TestArguments:
    @pytest.mark.parametrize("missing", _REQUIRED)
    def test_each_required_argument_is_named_when_missing(
        self, healthy: Layout, missing: str
    ) -> None:
        args = []
        for name in _REQUIRED:
            if name != missing:
                args += [name, "x"]
        env = dict(os.environ, PATH=f"{healthy.bin}:{os.environ['PATH']}")
        result = subprocess.run(
            [str(_SCRIPT), *args], capture_output=True, text=True, timeout=30, env=env
        )
        assert result.returncode == 2
        assert missing in result.stderr

    def test_unknown_argument_refused(self, healthy: Layout) -> None:
        result = healthy.run("--frobnicate")
        assert result.returncode == 2
        assert "unknown argument" in result.stderr

    def test_non_numeric_stale_after_refused(self, healthy: Layout) -> None:
        result = healthy.run("--stale-after", "soon")
        assert result.returncode == 2
        assert "--stale-after" in result.stderr


class TestInnerPassThrough:
    def test_healthy_is_inner_pass_plus_zero_flags(self, healthy: Layout) -> None:
        result = healthy.run()
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines() == [_INNER_PASS, "FLAGS archive=0 stale=0"]

    def test_inner_failures_are_passed_through_and_classed_archive(
        self, healthy: Layout
    ) -> None:
        lines = [
            "FAIL archive_mode_off: archive_mode is 'off' — archiving is not running",
            "FAIL wal_disk_low: 3% free on the filesystem holding /pgdata (floor 15%)",
        ]
        healthy.stub_inner(lines, 1)
        result = healthy.run()
        assert result.returncode == 1
        assert result.stdout.splitlines()[:2] == lines
        assert _flags_line(result.stdout) == "FLAGS archive=2 stale=0"

    def test_wrapper_continues_to_its_own_checks_after_inner_failure(
        self, healthy: Layout
    ) -> None:
        healthy.stub_inner(["FAIL archiver_failing: the most recent attempt failed"], 1)
        healthy.stamp.unlink()
        result = healthy.run()
        assert _fail_names(result.stdout) == ["archiver_failing", "offsite_wal_stale"]
        assert _flags_line(result.stdout) == "FLAGS archive=1 stale=1"

    def test_inner_exit_without_fail_line_is_an_archive_failure(
        self, healthy: Layout
    ) -> None:
        healthy.stub_inner(["psql: error: connection refused"], 2)
        result = healthy.run()
        assert result.returncode == 1
        assert "psql: error: connection refused" in result.stdout
        assert _fail_names(result.stdout) == ["archive_uncheckable"]
        assert _flags_line(result.stdout) == "FLAGS archive=1 stale=0"


class TestPrunePermission:
    def test_read_only_wal_dir_fails_and_leaves_no_canary(
        self, healthy: Layout
    ) -> None:
        healthy.wal.chmod(0o555)
        try:
            result = healthy.run()
        finally:
            healthy.wal.chmod(0o755)
        assert result.returncode == 1
        (fail_line,) = [
            line for line in result.stdout.splitlines()
            if line.startswith("FAIL prune_permission")
        ]  # fmt: skip
        assert str(healthy.wal) in fail_line
        assert os.environ.get("USER", "") in fail_line or "cannot create" in fail_line
        assert _flags_line(result.stdout) == "FLAGS archive=1 stale=0"
        assert list(healthy.wal.iterdir()) == []

    def test_writable_wal_dir_leaves_no_canary(self, healthy: Layout) -> None:
        result = healthy.run()
        assert result.returncode == 0
        assert list(healthy.wal.iterdir()) == []


class TestArchiveWedged:
    def test_short_file_at_next_segment_name_is_wedged(self, healthy: Layout) -> None:
        (healthy.wal / _NEXT_SEGMENT).write_bytes(b"\0" * 1000)
        result = healthy.run()
        assert result.returncode == 1
        assert _fail_names(result.stdout) == ["archive_wedged"]
        assert _NEXT_SEGMENT in result.stdout
        assert _flags_line(result.stdout) == "FLAGS archive=1 stale=0"

    def test_short_file_with_zst_sibling_is_not_wedged(self, healthy: Layout) -> None:
        (healthy.wal / _NEXT_SEGMENT).write_bytes(b"\0" * 1000)
        (healthy.wal / f"{_NEXT_SEGMENT}.zst").write_bytes(b"\x28\xb5\x2f\xfd")
        result = healthy.run()
        assert result.returncode == 0, result.stdout
        assert _fail_names(result.stdout) == []

    def test_whole_segment_at_next_name_is_not_wedged(self, healthy: Layout) -> None:
        with (healthy.wal / _NEXT_SEGMENT).open("wb") as f:
            f.truncate(_WHOLE_SEGMENT)
        result = healthy.run()
        assert result.returncode == 0, result.stdout

    def test_failing_archiver_uses_last_failed_wal(self, healthy: Layout) -> None:
        failed = "0000000100001217000000A0"
        healthy.stub_psql("t", _LAST_ARCHIVED, failed)
        (healthy.wal / failed).write_bytes(b"\0" * 1000)
        result = healthy.run()
        assert _fail_names(result.stdout) == ["archive_wedged"]
        assert failed in result.stdout

    def test_no_archived_segment_yet_skips_the_check(self, healthy: Layout) -> None:
        healthy.stub_psql("f", "", "")
        (healthy.wal / _NEXT_SEGMENT).write_bytes(b"\0" * 1000)
        result = healthy.run()
        assert result.returncode == 0, result.stdout


class TestTmpLeftover:
    def test_old_tmp_is_a_leftover(self, healthy: Layout) -> None:
        leftover = healthy.wal / "x.zst.tmp"
        leftover.touch()
        _age(leftover, minutes=20)
        result = healthy.run()
        assert result.returncode == 1
        assert _fail_names(result.stdout) == ["archive_tmp_leftover"]
        assert "x.zst.tmp" in result.stdout
        assert _flags_line(result.stdout) == "FLAGS archive=1 stale=0"

    def test_fresh_tmp_is_an_archive_in_progress(self, healthy: Layout) -> None:
        (healthy.wal / "x.zst.tmp").touch()
        result = healthy.run()
        assert result.returncode == 0, result.stdout


class TestStaleChecks:
    def test_aged_push_stamp(self, healthy: Layout) -> None:
        _age(healthy.stamp, minutes=181)
        result = healthy.run()
        assert result.returncode == 1
        assert _fail_names(result.stdout) == ["offsite_wal_stale"]
        assert _flags_line(result.stdout) == "FLAGS archive=0 stale=1"

    def test_push_stamp_within_limit_passes(self, healthy: Layout) -> None:
        _age(healthy.stamp, minutes=170)
        assert healthy.run().returncode == 0

    def test_missing_system_stamp(self, healthy: Layout) -> None:
        healthy.system_stamp.unlink()
        result = healthy.run()
        assert _fail_names(result.stdout) == ["system_backup_stale"]
        assert _flags_line(result.stdout) == "FLAGS archive=0 stale=1"

    def test_aged_system_stamp(self, healthy: Layout) -> None:
        _age(healthy.system_stamp, minutes=2 * 1440 + 1)
        result = healthy.run()
        assert _fail_names(result.stdout) == ["system_backup_stale"]

    def test_old_base_directory_by_name_not_mtime(self, healthy: Layout) -> None:
        for d in healthy.base.iterdir():
            d.rmdir()
        old = healthy.base / "20260101"
        old.mkdir()  # mtime is now; only the name says it is old
        result = healthy.run()
        assert _fail_names(result.stdout) == ["weekly_base_stale"]
        assert "20260101" in result.stdout
        assert _flags_line(result.stdout) == "FLAGS archive=0 stale=1"

    def test_base_eight_days_old_passes(self, healthy: Layout) -> None:
        for d in healthy.base.iterdir():
            d.rmdir()
        (healthy.base / (date.today() - timedelta(days=8)).strftime("%Y%m%d")).mkdir()
        assert healthy.run().returncode == 0

    def test_no_base_directory_at_all(self, healthy: Layout) -> None:
        for d in healthy.base.iterdir():
            d.rmdir()
        result = healthy.run()
        assert _fail_names(result.stdout) == ["weekly_base_stale"]


class TestSummaryLine:
    def test_mixed_faults_count_by_class(self, healthy: Layout) -> None:
        healthy.stub_inner(["FAIL archive_mode_off: archive_mode is 'off'"], 1)
        (healthy.wal / _NEXT_SEGMENT).write_bytes(b"\0" * 1000)
        healthy.stamp.unlink()
        healthy.system_stamp.unlink()
        result = healthy.run()
        assert result.returncode == 1
        assert _fail_names(result.stdout) == [
            "archive_mode_off",
            "archive_wedged",
            "offsite_wal_stale",
            "system_backup_stale",
        ]
        assert _flags_line(result.stdout) == "FLAGS archive=2 stale=2"

    def test_summary_is_always_the_last_line(self, healthy: Layout) -> None:
        healthy.stub_inner(["FAIL archiver_failing: x"], 1)
        result = healthy.run()
        assert _flags_line(result.stdout).startswith("FLAGS archive=")


# --- backup_health_cron.sh: the two-flag glue (Task 1.8) -----------------------

_GLUE = _REPO_ROOT / "scripts" / "backup_health_cron.sh"
_GLUE_REQUIRED = (
    "--env-file",
    "--pgdata",
    "--wal-dir",
    "--stamp",
    "--stale-after",
    "--system-stamp",
    "--base-dir",
    "--flag",
    "--stale-flag",
    "--log",
)


@dataclass
class GlueLayout:
    root: Path
    bin: Path
    env_file: Path
    flag: Path
    stale_flag: Path
    log: Path
    journal: Path

    def stub_checker(self, lines: list[str], rc: int) -> None:
        _write_stub(self.bin, "check_backup_health.sh", lines, rc)

    def run(self) -> subprocess.CompletedProcess[str]:
        args = [
            "--env-file", str(self.env_file),
            "--pgdata", str(self.root / "pgdata"),
            "--wal-dir", str(self.root / "wal"),
            "--stamp", str(self.root / "stamp"),
            "--stale-after", "180",
            "--system-stamp", str(self.root / "system-stamp"),
            "--base-dir", str(self.root / "base"),
            "--flag", str(self.flag),
            "--stale-flag", str(self.stale_flag),
            "--log", str(self.log),
        ]  # fmt: skip
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")
        return subprocess.run(
            [str(_GLUE), *args], capture_output=True, text=True, timeout=60, env=env
        )

    def journal_lines(self) -> list[str]:
        if not self.journal.exists():
            return []
        return self.journal.read_text().splitlines()


@pytest.fixture
def glue(tmp_path: Path) -> GlueLayout:
    """Glue with the checker and ``logger`` stubbed; the env file holds a URL."""
    layout = GlueLayout(
        root=tmp_path,
        bin=tmp_path / "bin",
        env_file=tmp_path / "env",
        flag=tmp_path / "ARCHIVE-BROKEN",
        stale_flag=tmp_path / "BACKUP-STALE",
        log=tmp_path / "health.log",
        journal=tmp_path / "journal",
    )
    layout.bin.mkdir()
    layout.env_file.write_text(
        'MT_TIMESCALE_MAINTENANCE_URL="postgresql://stub/none"\n'
    )
    # logger(1) stub: append its message to a file so transitions can be counted.
    stub = layout.bin / "logger"
    stub.write_text(
        '#!/usr/bin/env bash\nshift 2\necho "$*" >> ' + repr(str(layout.journal)) + "\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    layout.stub_checker([_INNER_PASS, "FLAGS archive=0 stale=0"], 0)
    return layout


class TestGlueArguments:
    @pytest.mark.parametrize("missing", _GLUE_REQUIRED)
    def test_each_required_argument_is_named_when_missing(self, missing: str) -> None:
        args = []
        for name in _GLUE_REQUIRED:
            if name != missing:
                args += [name, "x"]
        result = subprocess.run(
            [str(_GLUE), *args], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 2
        assert missing in result.stderr

    def test_unknown_argument_refused(self) -> None:
        result = subprocess.run(
            [str(_GLUE), "--frobnicate"], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 2
        assert "unknown argument" in result.stderr


class TestGlueFlags:
    def test_healthy_writes_no_flag_and_one_log_line(self, glue: GlueLayout) -> None:
        result = glue.run()
        assert result.returncode == 0, result.stderr
        assert not glue.flag.exists()
        assert not glue.stale_flag.exists()
        assert len(glue.log.read_text().splitlines()) == 1
        assert glue.journal_lines() == []

    def test_stale_only_writes_stale_flag_not_archive_flag(
        self, glue: GlueLayout
    ) -> None:
        glue.stub_checker(
            [
                _INNER_PASS,
                "FAIL offsite_wal_stale: stamp is 200 min old",
                "FLAGS archive=0 stale=1",
            ],
            1,
        )
        result = glue.run()
        assert result.returncode == 1
        assert glue.stale_flag.exists()
        assert not glue.flag.exists()
        assert "offsite_wal_stale" in glue.stale_flag.read_text()
        assert glue.journal_lines() == ["BACKUP-STALE raised: offsite_wal_stale"]

    def test_archive_only_writes_archive_flag_not_stale_flag(
        self, glue: GlueLayout
    ) -> None:
        glue.stub_checker(
            ["FAIL archive_wedged: short segment", "FLAGS archive=1 stale=0"], 1
        )
        result = glue.run()
        assert result.returncode == 1
        assert glue.flag.exists()
        assert not glue.stale_flag.exists()
        assert glue.flag.read_text().startswith("WAL ARCHIVING IS BROKEN")
        assert glue.journal_lines() == ["ARCHIVE-BROKEN raised: archive_wedged"]

    def test_both_cleared_on_healthy_run(self, glue: GlueLayout) -> None:
        glue.flag.write_text("stale content")
        glue.stale_flag.write_text("stale content")
        result = glue.run()
        assert result.returncode == 0
        assert not glue.flag.exists()
        assert not glue.stale_flag.exists()
        assert sorted(glue.journal_lines()) == [
            "ARCHIVE-BROKEN cleared",
            "BACKUP-STALE cleared",
        ]

    def test_journal_line_only_on_transition(self, glue: GlueLayout) -> None:
        glue.stub_checker(["FAIL archive_wedged: x", "FLAGS archive=1 stale=0"], 1)
        glue.run()
        glue.run()
        assert glue.journal_lines() == ["ARCHIVE-BROKEN raised: archive_wedged"]
        assert len(glue.log.read_text().splitlines()) == 2

    def test_missing_url_writes_archive_flag(self, glue: GlueLayout) -> None:
        glue.env_file.write_text("OTHER=1\n")
        result = glue.run()
        assert result.returncode == 1
        assert glue.flag.exists()
        assert "cannot_check" in glue.flag.read_text()
        assert not glue.stale_flag.exists()

    def test_checker_without_summary_writes_archive_flag(
        self, glue: GlueLayout
    ) -> None:
        glue.stub_checker(["bash: something exploded"], 127)
        result = glue.run()
        assert result.returncode == 1
        assert glue.flag.exists()
        assert "cannot_check" in glue.flag.read_text()
        assert "something exploded" in glue.flag.read_text()
