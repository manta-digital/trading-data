"""An ``ohlcv-1m`` unit's file lives in the existing archive layout (slice 302, D9)."""

from __future__ import annotations

from pathlib import Path

from manta_trading.data.tick.in_flight_files import _unit_file

JOB = "GLBX-20261103-BARJOB"


def test_a_bar_unit_path_is_job_relative_and_resolves_under_the_archive_root(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    downloaded = archive / JOB / "glbx-mdp3-20241203.ohlcv-1m.dbn.zst"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"bars")
    unit_file = _unit_file(JOB, downloaded)
    assert unit_file.path == f"{JOB}/glbx-mdp3-20241203.ohlcv-1m.dbn.zst"
    resolved = (archive / unit_file.path).resolve()
    assert resolved == downloaded.resolve()
    assert resolved.is_relative_to(archive.resolve())
