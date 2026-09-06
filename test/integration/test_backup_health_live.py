"""One live run of ``check_backup_health.sh`` against the test cluster (920, 1.8).

The unit tier stubs ``check_archive_health.sh``; this is the one place the
real inner script runs under the wrapper. The test cluster has
``archive_mode=off``, so the expected shape is: the inner script's
``archive_mode_off`` FAIL passes through verbatim and is counted in the
``archive`` class, while the wrapper's own six checks all pass on a healthy
``tmp_path`` layout. Read-only against the cluster.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts" / "check_backup_health.sh"


def test_inner_lines_pass_through_and_archive_mode_off_is_archive_class(
    tmp_path: Path, test_admin_url: str
) -> None:
    wal = tmp_path / "wal"
    base = tmp_path / "base"
    wal.mkdir()
    (base / date.today().strftime("%Y%m%d")).mkdir(parents=True)
    stamp = tmp_path / "wal-offsite.stamp"
    system_stamp = tmp_path / "system-backup.stamp"
    stamp.touch()
    system_stamp.touch()

    result = subprocess.run(
        [
            str(_SCRIPT),
            "--db-url",
            test_admin_url,
            "--pgdata",
            str(tmp_path),
            "--wal-dir",
            str(wal),
            "--stamp",
            str(stamp),
            "--stale-after",
            "180",
            "--system-stamp",
            str(system_stamp),
            "--base-dir",
            str(base),
        ],  # fmt: skip
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = result.stdout.splitlines()
    fails = [line for line in lines if line.startswith("FAIL ")]
    assert result.returncode == 1, result.stdout + result.stderr
    assert len(fails) == 1, lines
    assert fails[0].startswith("FAIL archive_mode_off: archive_mode is 'off'"), fails
    assert lines[-1] == "FLAGS archive=1 stale=0", lines
    assert list(wal.iterdir()) == [], "the canary must not be left behind"
