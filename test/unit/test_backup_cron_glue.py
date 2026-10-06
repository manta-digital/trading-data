"""Tests for the slice-915 retention script and nightly glue (no database).

The load-bearing cases: pruning never deletes the newest backup however old
it is, keys the WAL cutoff to the oldest *retained* backup's manifest, and
refuses an empty backup directory. The 915 health and weekly glue were
superseded by cron.d in slice 920 (``test_backup_health.py``,
``test_wal_offsite.py``) and deleted after the cutover.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

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


# --- cron_nightly_metadata.sh --url-key / --remote (slice 227, task 2.2) -------

_MAIN_URL = "postgresql://main/db"
_TICK_URL = "postgresql://tick/db"


@pytest.fixture
def metadata_host(tmp_path: Path) -> dict[str, Path]:
    """psql/pg_dump/rclone stubs that record their target; two URLs in env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    stubs = {
        "psql": f'echo "psql $1" >> {str(calls)!r}; echo some_table\n',
        "pg_dump": (
            f'echo "pg_dump $1" >> {str(calls)!r}\n'
            'while [ $# -gt 0 ]; do [ "$1" = -f ] && : > "$2"; shift; done\n'
        ),
        "rclone": f'echo "rclone $*" >> {str(calls)!r}\n',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text("#!/usr/bin/env bash\n" + body)
        stub.chmod(0o755)
    env = tmp_path / "env"
    env.write_text(
        f'MT_TIMESCALE_MAINTENANCE_URL="{_MAIN_URL}"\n'
        f'MT_TICK_MAINTENANCE_URL="{_TICK_URL}"\n'
        'MT_BACKUP_S3_BUCKET="bucket-x"\n'
    )
    return {"bin": bin_dir, "calls": calls, "env": env, "dest": tmp_path / "meta"}


def _metadata(h: dict[str, Path], *extra: str) -> subprocess.CompletedProcess[str]:
    args = ["--env-file", str(h["env"]), "--dest", str(h["dest"])]
    args += ["--url-key", "MT_TIMESCALE_MAINTENANCE_URL", "--remote", "b2:bucket-x"]
    args += extra  # last, so a test's own values win
    env = dict(os.environ, PATH=f"{h['bin']}:{os.environ['PATH']}")
    return subprocess.run(
        [str(_SCRIPTS / "cron_nightly_metadata.sh"), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _metadata_calls(h: dict[str, Path], tool: str) -> list[str]:
    lines = h["calls"].read_text().splitlines() if h["calls"].exists() else []
    return [x.split(" ", 1)[1] for x in lines if x.startswith(f"{tool} ")]


class TestNightlyMetadataArguments:
    def test_production_arguments(self, metadata_host: dict[str, Path]) -> None:
        result = _metadata(metadata_host)
        assert result.returncode == 0, result.stderr
        assert _metadata_calls(metadata_host, "pg_dump") == [_MAIN_URL]
        rclone = _metadata_calls(metadata_host, "rclone")
        assert rclone and all("b2:bucket-x/metadata" in c.split() for c in rclone)

    def test_url_key_and_remote_pick_the_tick_cluster(
        self, metadata_host: dict[str, Path]
    ) -> None:
        result = _metadata(
            metadata_host,
            "--url-key", "MT_TICK_MAINTENANCE_URL",
            "--remote", "b2:x/17-tick",
        )  # fmt: skip
        assert result.returncode == 0, result.stderr
        assert _metadata_calls(metadata_host, "psql") == [_TICK_URL]
        assert _metadata_calls(metadata_host, "pg_dump") == [_TICK_URL]
        rclone = _metadata_calls(metadata_host, "rclone")
        assert rclone and all("b2:x/17-tick/metadata" in c.split() for c in rclone)

    @pytest.mark.parametrize("missing", ["--url-key", "--remote"])
    def test_missing_required_argument_exits_2(
        self, metadata_host: dict[str, Path], missing: str
    ) -> None:
        """Task 8.3: no pre-227 fallback to production's key or bucket."""
        h = metadata_host
        args = {
            "--env-file": str(h["env"]),
            "--dest": str(h["dest"]),
            "--url-key": "MT_TIMESCALE_MAINTENANCE_URL",
            "--remote": "b2:x",
        }
        argv = [a for k, v in args.items() if k != missing for a in (k, v)]
        result = subprocess.run(
            [str(_SCRIPTS / "cron_nightly_metadata.sh"), *argv],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 2
        assert f"{missing} is required" in result.stderr
        assert "usage:" in result.stderr
        assert not h["calls"].exists()

    @pytest.mark.parametrize("key", ["mt_lower", "TIMESCALE_URL", "MT_A;rm"])
    def test_bad_key_exits_2(self, metadata_host: dict[str, Path], key: str) -> None:
        result = _metadata(metadata_host, "--url-key", key)
        assert result.returncode == 2
        assert "--url-key" in result.stderr
        assert not metadata_host["calls"].exists()

    def test_missing_key_is_named(self, metadata_host: dict[str, Path]) -> None:
        result = _metadata(metadata_host, "--url-key", "MT_NOT_THERE_URL")
        assert result.returncode == 1
        assert "MT_NOT_THERE_URL not in" in result.stderr


# The read cron_nightly_metadata.sh used before 227, verbatim.
_OLD_READ = "grep '^{key}' \"$1\" | sed 's/^[^=]*=//' | tr -d '\"'"
_NEW_READ = f'. "{_REPO_ROOT}/deploy/lib/env_value.sh"; env_value "$1" {{key}}'
_KEY = "MT_TIMESCALE_MAINTENANCE_URL"


def _read(template: str, env_file: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", template.format(key=_KEY), "_", str(env_file)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


class TestMetadataUrlReadParity:
    """Task 2.2 swapped the grep|sed|tr read for env_value: same value for
    every single-line shape a real env file holds."""

    @pytest.mark.parametrize(
        "text",
        [
            f'{_KEY}="postgresql://u:p@h:5432/db"\n',
            f"{_KEY}=postgresql://u:p@h:5432/db\n",
            f'{_KEY}="postgresql://u:p@h:5432/db"   \n',
            f'OTHER=1\n{_KEY}="postgresql://u:p$x@h/db"\nMORE="2"\n',
            f'# comment\n\n{_KEY}="postgresql://u:p=q@h/db"',
        ],
        ids=["quoted", "unquoted", "trailing-space", "among-keys", "eq-in-pw"],
    )
    def test_same_value(self, tmp_path: Path, text: str) -> None:
        env = tmp_path / "env"
        env.write_text(text)
        old = _read(_OLD_READ, env)
        assert old.strip() != ""
        assert _read(_NEW_READ, env) == old

    @pytest.mark.parametrize(
        "text",
        [
            f'{_KEY}="postgresql://first/db"\n{_KEY}="postgresql://second/db"\n',
            f'{_KEY}_OLD="postgresql://old/db"\n{_KEY}="postgresql://first/db"\n',
        ],
        ids=["duplicate-key", "prefix-sharing-key"],
    )
    def test_old_read_was_broken_where_they_differ(
        self, tmp_path: Path, text: str
    ) -> None:
        """The only differences: the old unanchored read returned two lines
        (an unusable URL); env_value returns the one `KEY=` line it should.
        The production .env holds exactly one line per key (checked 2026-10-04)."""
        env = tmp_path / "env"
        env.write_text(text)
        assert len(_read(_OLD_READ, env).splitlines()) == 2
        assert _read(_NEW_READ, env).strip() == "postgresql://first/db"
