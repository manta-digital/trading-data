"""The day-file helper writes headers the reader accepts as one UTC day (223)."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from tick_support.dbn_files import (
    JOB_JSON_FILES,
    MANIFEST_NAME,
    day_file_bytes,
    write_day_file,
    write_job_dir,
    zip_job_dir,
)

from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema
from manta_trading.data.tick.databento.dbn_file import DbnFileReader

DAY = date(2024, 9, 3)

#: fixture, schema, records in the fixture.
FIXTURE_FILES = [
    ("test_data.trades.v3.dbn.zst", TickSchema.TRADES, 2),
    ("test_data.tbbo.v3.dbn.zst", TickSchema.TBBO, 2),
    ("test_data.definition.v3.dbn.zst", TickSchema.DEFINITION, 2),
]


@pytest.mark.parametrize(("fixture", "schema", "count"), FIXTURE_FILES)
def test_day_file_reads_back_as_one_day(
    tmp_path: Path, fixture: str, schema: TickSchema, count: int
) -> None:
    written = write_day_file(
        tmp_path / "day.dbn.zst", fixture, CME_DATASET, DAY, SType.PARENT
    )
    tick_file = DbnFileReader().open_file(written.path)
    assert tick_file.dataset == CME_DATASET
    assert tick_file.schema is schema
    assert tick_file.stype_in is SType.PARENT
    assert tick_file.start == datetime(2024, 9, 3, tzinfo=UTC)
    assert tick_file.end == datetime(2024, 9, 4, tzinfo=UTC)
    assert sum(batch.count for batch in tick_file.iter_batches()) == count
    assert written.size == written.path.stat().st_size


def test_job_dir_manifest_lists_every_other_file(tmp_path: Path) -> None:
    day = day_file_bytes("test_data.trades.v3.dbn.zst", CME_DATASET, DAY, SType.PARENT)
    job_dir = write_job_dir(tmp_path, "GLBX-TEST", {"d.dbn.zst": day})
    manifest = json.loads((job_dir / MANIFEST_NAME).read_text())
    listed = {entry["filename"] for entry in manifest["files"]}
    assert manifest["job_id"] == "GLBX-TEST"
    assert listed == {"d.dbn.zst", *JOB_JSON_FILES}
    assert all(entry["hash"].startswith("sha256:") for entry in manifest["files"])


def test_zip_is_flat(tmp_path: Path) -> None:
    job_dir = write_job_dir(tmp_path, "GLBX-TEST", {"d.dbn.zst": b"x"})
    with zipfile.ZipFile(zip_job_dir(job_dir)) as zf:
        assert set(zf.namelist()) == {p.name for p in job_dir.iterdir()}
