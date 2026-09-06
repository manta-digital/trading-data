"""Tests for the slice-915 retention script and nightly glue (no database).

The load-bearing cases: pruning never deletes the newest backup however old
it is, keys the WAL cutoff to the oldest *retained* backup's manifest, and
refuses an empty backup directory. The 915 health and weekly glue were
superseded by cron.d in slice 920 (``test_backup_health.py``,
``test_wal_offsite.py``) and deleted after the cutover.
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


def test_nightly_glue_refuses_missing_arguments() -> None:
    result = _run("cron_nightly_metadata.sh")
    assert result.returncode != 0
    assert "--env-file" in result.stderr


class TestPruneMixedArchive:
    """Slice 920, Task 4.2: a mixed raw/.zst archive prunes by segment name
    with the extension stripped, against the real pg_archivecleanup."""

    def test_prunes_raw_and_zst_below_cutoff_and_reports_counts(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "base"
        wal = tmp_path / "wal"
        wal.mkdir()
        _make_backup(base, "20250101", "11A6/50000028")
        _make_backup(base, "20250110", "11A6/73000028")  # cutoff segment …73
        old_raw = ("70", "71")
        old_zst = ("72",)
        new_raw = ("73",)
        new_zst = ("74", "75")
        for seg in old_raw + new_raw:
            (wal / f"00000001000011A6000000{seg}").touch()
        for seg in old_zst + new_zst:
            (wal / f"00000001000011A6000000{seg}.zst").touch()
        # A backup-history file older than the cutoff (pg_archivecleanup keeps
        # history files: only segment names are eligible), and the health
        # check's canary (a non-segment name it must ignore).
        (wal / "00000001000011A600000071.00000028.backup").touch()
        (wal / ".prune-canary.tmp").touch()

        result = _run(
            "prune_wal_archive.sh",
            "--base-dir", str(base),
            "--wal-dir", str(wal),
            "--keep-days", "21",
        )  # fmt: skip
        assert result.returncode == 0, result.stderr
        remaining = sorted(p.name for p in wal.iterdir())
        assert remaining == [
            ".prune-canary.tmp",
            "00000001000011A600000071.00000028.backup",
            "00000001000011A600000073",
            "00000001000011A600000074.zst",
            "00000001000011A600000075.zst",
        ], remaining
        assert not (base / "20250101").exists()
        # 2 old raw + 1 old .zst pruned; the .backup history file is kept.
        assert result.stdout.rstrip().splitlines()[-1] == "PRUNED wal=3 base=1"

    def test_pruned_line_is_zero_when_nothing_is_old(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        wal = tmp_path / "wal"
        wal.mkdir()
        _make_backup(base, "20250110", "11A6/73000028")
        (wal / "00000001000011A600000073.zst").touch()
        result = _run(
            "prune_wal_archive.sh",
            "--base-dir", str(base),
            "--wal-dir", str(wal),
            "--keep-days", "21",
        )  # fmt: skip
        assert result.returncode == 0, result.stderr
        assert result.stdout.rstrip().splitlines()[-1] == "PRUNED wal=0 base=0"
