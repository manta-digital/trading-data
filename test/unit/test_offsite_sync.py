"""Failure-path tests for scripts/offsite_sync.sh (slice 915, task 6.4).

Run against local rclone paths — no object store needed. The load-bearing
assertion: a corruption that keeps size and modtime identical sails through
``rclone sync``'s quick pass and is caught ONLY by the checksum check. That
is what proves the check stage is not decorative.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "offsite_sync.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SCRIPT), *args], capture_output=True, text=True, timeout=120
    )


def _make_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "base.tar.gz").write_bytes(b"pretend-backup-content")
    (source / "backup_manifest").write_bytes(b"manifest")
    return source


def test_missing_arguments_refused(tmp_path: Path) -> None:
    assert _run().returncode != 0
    assert "--source" in _run().stderr
    only_source = _run("--source", str(tmp_path))
    assert only_source.returncode != 0
    assert "--remote" in only_source.stderr


def test_sync_and_check_round_trip(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    remote = tmp_path / "remote"
    result = _run("--source", str(source), "--remote", str(remote))
    assert result.returncode == 0, result.stderr
    assert (remote / "base.tar.gz").read_bytes() == b"pretend-backup-content"


def test_invalid_remote_fails(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    result = _run("--source", str(source), "--remote", ":nonexistentbackend:whatever")
    assert result.returncode != 0
    assert "verified by checksum" not in result.stdout


def test_checksum_catches_silent_corruption(tmp_path: Path) -> None:
    """Corrupt the remote copy without changing size or mtime.

    ``rclone sync`` quick-pass (size+modtime) sees nothing to do; only the
    ``rclone check`` checksum pass can catch it — so the whole invocation
    must fail.
    """
    source = _make_source(tmp_path)
    remote = tmp_path / "remote"
    assert _run("--source", str(source), "--remote", str(remote)).returncode == 0

    victim = remote / "base.tar.gz"
    stat = victim.stat()
    corrupted = b"X" * len(b"pretend-backup-content")
    victim.write_bytes(corrupted)
    os.utime(victim, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    result = _run("--source", str(source), "--remote", str(remote))
    assert result.returncode != 0, "checksum check failed to catch silent corruption"
    assert "verified by checksum" not in result.stdout
