"""Tests for the slice-915 cron glue and retention scripts (no database).

The load-bearing cases: pruning never deletes the newest backup however old
it is, keys the WAL cutoff to the oldest *retained* backup's manifest, and
refuses an empty backup directory; the weekly glue refuses to run while the
archive-health flag exists; the health glue turns "cannot check" into an
alarm rather than silence.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SCRIPTS / script), *args], capture_output=True, text=True, timeout=60
    )


def _make_backup(base_dir: Path, name: str, start_lsn: str) -> None:
    d = base_dir / name
    d.mkdir(parents=True)
    manifest = {
        "WAL-Ranges": [{"Timeline": 1, "Start-LSN": start_lsn, "End-LSN": start_lsn}]
    }
    (d / "backup_manifest").write_text(json.dumps(manifest))


class TestPruneWalArchive:
    def test_refuses_missing_arguments(self) -> None:
        assert _run("prune_wal_archive.sh").returncode != 0
        assert "--base-dir" in _run("prune_wal_archive.sh").stderr

    def test_refuses_empty_base_dir(self, tmp_path: Path) -> None:
        wal = tmp_path / "wal"
        wal.mkdir()
        result = _run(
            "prune_wal_archive.sh",
            "--base-dir",
            str(tmp_path / "base"),
            "--wal-dir",
            str(wal),
            "--keep-days",
            "21",
        )
        assert result.returncode != 0
        assert "refusing" in result.stderr

    def test_prunes_by_manifest_and_keeps_newest(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        wal = tmp_path / "wal"
        wal.mkdir()
        # Two backups, both far older than any keep window. The older one must
        # be pruned; the newest must survive on the always-keep rule.
        _make_backup(base, "20250101", "11A6/50000028")
        _make_backup(base, "20250110", "11A6/73000028")
        # Segments before the retained backup's start (…73) are prunable.
        for seg in ("70", "71", "72", "73", "74"):
            (wal / f"00000001000011A6000000{seg}").touch()

        result = _run(
            "prune_wal_archive.sh",
            "--base-dir",
            str(base),
            "--wal-dir",
            str(wal),
            "--keep-days",
            "21",
        )
        assert result.returncode == 0, result.stderr
        assert not (base / "20250101").exists(), "old backup should be pruned"
        assert (base / "20250110").exists(), "newest backup must never be pruned"
        remaining = {p.name[-2:] for p in wal.iterdir()}
        assert remaining == {"73", "74"}, f"wrong segments retained: {remaining}"


class TestWeeklyGlueRefusals:
    def test_refuses_missing_arguments(self) -> None:
        result = _run("cron_weekly_base.sh")
        assert result.returncode != 0
        assert "--env-file" in result.stderr

    def test_refuses_when_health_flag_exists(self, tmp_path: Path) -> None:
        flag = tmp_path / "ARCHIVE-BROKEN"
        flag.write_text("boom")
        env = tmp_path / "env"
        env.write_text("MT_TIMESCALE_MAINTENANCE_URL=postgresql://x@localhost/x\n")
        result = _run(
            "cron_weekly_base.sh",
            "--env-file",
            str(env),
            "--base-dir",
            str(tmp_path / "base"),
            "--wal-dir",
            str(tmp_path / "wal"),
            "--keep-days",
            "21",
            "--health-flag",
            str(flag),
        )
        assert result.returncode != 0
        assert "unhealthy" in result.stderr
        # Refusal must happen before any backup side effects.
        assert not (tmp_path / "base").exists()


class TestHealthGlue:
    def test_refuses_missing_arguments(self) -> None:
        result = _run("archive_health_cron.sh")
        assert result.returncode != 0
        assert "--env-file" in result.stderr

    def test_uncheckable_is_an_alarm_not_silence(self, tmp_path: Path) -> None:
        env = tmp_path / "env"
        env.write_text("SOMETHING_ELSE=1\n")  # no maintenance URL at all
        flag = tmp_path / "ARCHIVE-BROKEN"
        log = tmp_path / "health.log"
        result = _run(
            "archive_health_cron.sh",
            "--env-file",
            str(env),
            "--pgdata",
            str(tmp_path),
            "--flag",
            str(flag),
            "--log",
            str(log),
        )
        assert result.returncode != 0
        assert flag.exists(), "cannot-check must raise the flag"
        assert "cannot_check" in flag.read_text()
        assert log.exists() and "cannot_check" in log.read_text()


def test_nightly_glue_refuses_missing_arguments() -> None:
    result = _run("cron_nightly_metadata.sh")
    assert result.returncode != 0
    assert "--env-file" in result.stderr
