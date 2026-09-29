"""Adoption file handling over job directories and zips (223; LLD 224 TD10)."""

from __future__ import annotations

import errno
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tick_support.dbn_files import (
    FIXTURES,
    JOB_JSON_FILES,
    MANIFEST_NAME,
    day_file_bytes,
    write_job_dir,
    zip_job_dir,
)

from manta_trading.data.tick.adopt_files import (
    PARTIAL_SUFFIX,
    TickAdoptionRefused,
    TickArchiveWriteError,
    _read_manifest,
    _Source,
    archive_job_files,
)
from manta_trading.data.tick.constants import CME_DATASET, SType

JOB = "GLBX-20240930-TESTJOB"
DAY_FILE = "glbx-mdp3-20240903.trades.dbn.zst"
PLENTY = 10**12
REAL_JOB = "GLBX-20240930-USM7UXXJBA"
REAL_MANIFEST_DIR = FIXTURES / "batch" / REAL_JOB


def _plenty(path: Path) -> int:
    return PLENTY


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    content = day_file_bytes(
        "test_data.trades.v3.dbn.zst", CME_DATASET, date(2024, 9, 3), SType.PARENT
    )
    return write_job_dir(tmp_path / "source", JOB, {DAY_FILE: content})


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    return root


def _archive(source: Path, archive: Path, **kw: Any) -> list[Any]:
    return archive_job_files(source, JOB, archive, free=kw.get("free", _plenty))


def _expected_names() -> set[str]:
    return {DAY_FILE, MANIFEST_NAME, *JOB_JSON_FILES}


@pytest.mark.parametrize("as_zip", [False, True])
def test_directory_and_zip_copy_every_file(
    job_dir: Path, archive: Path, as_zip: bool
) -> None:
    source = zip_job_dir(job_dir) if as_zip else job_dir
    archived = _archive(source, archive)
    assert {f.name for f in archived} == _expected_names()
    assert all(f.copied for f in archived)
    for name in _expected_names():
        assert (archive / JOB / name).read_bytes() == (job_dir / name).read_bytes()


def test_adopting_from_the_archive_copies_nothing(job_dir: Path, archive: Path) -> None:
    _archive(job_dir, archive)
    again = _archive(archive / JOB, archive)
    assert not any(f.copied for f in again)


def _flip_last_byte(path: Path) -> None:
    content = bytearray(path.read_bytes())
    content[-1] ^= 0xFF
    path.write_bytes(bytes(content))


def test_flipped_byte_refuses_naming_the_file(job_dir: Path, archive: Path) -> None:
    _flip_last_byte(job_dir / DAY_FILE)
    with pytest.raises(TickAdoptionRefused, match=DAY_FILE):
        _archive(job_dir, archive)
    assert not (archive / JOB / DAY_FILE).exists()
    assert (archive / JOB / (DAY_FILE + PARTIAL_SUFFIX)).exists()


def _relist(job_dir: Path, mutate: Any) -> None:
    path = job_dir / MANIFEST_NAME
    body = json.loads(path.read_text())
    mutate(body)
    path.write_text(json.dumps(body))


def test_traversal_name_is_refused(job_dir: Path, archive: Path) -> None:
    _relist(job_dir, lambda b: b["files"][0].update(filename="../x"))
    with pytest.raises(TickAdoptionRefused, match="unsafe name '../x'"):
        _archive(job_dir, archive)


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda b: b["files"][0].pop("size"), "size"),
        (lambda b: b["files"][0].pop("hash"), "hash"),
        (lambda b: b["files"][0].update(size="large"), "size"),
        (lambda b: b["files"].append("condition.json"), "files"),
        (lambda b: b.pop("job_id"), "job_id"),
    ],
    ids=["no-size", "no-hash", "text-size", "bare-string-entry", "no-job-id"],
)
def test_malformed_manifest_is_refused(
    job_dir: Path, archive: Path, mutate: Any, field: str
) -> None:
    _relist(job_dir, mutate)
    with pytest.raises(TickAdoptionRefused, match=f"unreadable(.|\n)*{field}"):
        _archive(job_dir, archive)
    assert not (archive / JOB).exists()


def test_real_provider_manifest_parses() -> None:
    """The manifest of a job the provider delivered, as it was downloaded."""
    listed = _read_manifest(_Source(REAL_MANIFEST_DIR), REAL_JOB)[1]
    assert {item.name for item in listed} >= {"condition.json", "metadata.json"}
    assert all(len(item.sha256) == 64 for item in listed)


def test_name_missing_from_zip_is_refused(job_dir: Path, archive: Path) -> None:
    extra = {"filename": "not-there.dbn.zst", "size": 1, "hash": "sha256:00"}
    _relist(job_dir, lambda b: b["files"].append(extra))
    with pytest.raises(TickAdoptionRefused, match="not-there.dbn.zst is listed"):
        _archive(zip_job_dir(job_dir), archive)


def test_job_id_mismatch_is_refused(job_dir: Path, archive: Path) -> None:
    with pytest.raises(TickAdoptionRefused, match="not --job-id GLBX-OTHER"):
        archive_job_files(job_dir, "GLBX-OTHER", archive, free=_plenty)


def test_insufficient_space_is_refused_before_any_copy(
    job_dir: Path, archive: Path
) -> None:
    with pytest.raises(TickAdoptionRefused, match="short"):
        _archive(job_dir, archive, free=lambda path: 10)
    assert not (archive / JOB).exists()


def test_write_failure_raises_the_storage_error(
    job_dir: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = Path.open

    def full_disk(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if self.name.endswith(PARTIAL_SUFFIX) and "w" in mode:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", full_disk)
    with pytest.raises(TickArchiveWriteError) as info:
        _archive(job_dir, archive)
    assert info.value.errno == errno.ENOSPC
    assert PARTIAL_SUFFIX in str(info.value.path)
    assert "errno 28" in str(info.value)
