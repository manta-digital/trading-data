"""Selectable tier units built from the real DBN slices (slice 225).

``test/fixtures/databento/real/`` holds small real cuts of adopted days (see
``scripts/cut_tick_fixtures.py``). ``seed_tier_unit`` lays one out as a batch
job under the archive root and seeds what ingest selection requires (LLD 225
Technical Decision 4): a *verified* tier unit with ``provider_record_count``
set, and its companion definition unit (same dataset, symbols, ``stype_in``
and day) projected into ``tick_definition`` by 224's own projection, which
leaves it *ingested*.

Kept apart from ``dbn_files.py``: that module re-heads the synthetic
fixtures; this one serves the real ones unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from tick_support.dbn_files import FIXTURES, JobFile, bad_header_bytes, write_job_dir
from tick_support.fake_provider import FakeClock, FakeTickProvider
from tick_support.rows import insert_request, insert_unit
from tick_support.runs import connect, tick_run

from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.definitions import project_definitions

REAL = FIXTURES / "real"
#: The days the real slices cover: the adopted trades job's largest day, and a
#: day of the adopted tbbo job (which also has a derived trades file, FR6).
TRADES_DAY = date(2024, 9, 3)
TBBO_DAY = date(2024, 12, 3)
SEEDED_AT = datetime(2026, 9, 30, tzinfo=UTC)
READER = DbnFileReader()


@dataclass(frozen=True)
class SeededUnit:
    unit_id: int
    definition_unit_id: int
    file: JobFile
    record_count: int


def real_file(day: date, schema: TickSchema) -> Path:
    return REAL / f"glbx-mdp3-{day:%Y%m%d}.{schema.value}.dbn.zst"


def _job_file(path: Path) -> JobFile:
    content = path.read_bytes()
    return JobFile(path, len(content), hashlib.sha256(content).hexdigest())


def record_count(path: Path) -> int:
    """Every record in the file, through the reader ingest uses."""
    return sum(batch.count for batch in READER.open_file(path).iter_batches())


def _unit(
    conn: psycopg.Connection[Any],
    schema: TickSchema,
    day: date,
    file: JobFile,
    archive_root: Path,
    **unit: Any,
) -> int:
    request = insert_request(
        conn,
        schema=schema.value,
        range_start=day,
        range_end=day + timedelta(days=1),
        requested_at=SEEDED_AT,
    )
    return insert_unit(
        conn,
        request,
        unit_date=day,
        state=UnitState.VERIFIED.value,
        file_path=str(file.path.relative_to(archive_root)),
        file_size_bytes=file.size,
        file_sha256=file.sha256,
        **unit,
    )


def _tier_bytes(tier: Path, bad_header: str | None) -> bytes:
    content = tier.read_bytes()
    return content if bad_header is None else bad_header_bytes(content, bad_header)


async def seed_tier_unit(
    url: str,
    archive_root: Path,
    schema: TickSchema,
    day: date,
    *,
    job_id: str,
    provider_record_count: int | None = None,
    tier_file: Path | None = None,
    definition_file: Path | None = None,
    bad_header: str | None = None,
) -> SeededUnit:
    """Seed a selectable ``schema`` unit for ``day`` from its real slice.

    ``provider_record_count`` defaults to the file's true count; a test of the
    counts check passes a different one. ``tier_file`` and ``definition_file``
    replace the committed slices (the load test uses whole archived days).
    ``bad_header`` (a ``BAD_HEADERS`` kind) archives the tier file with that
    header field rewritten; ``record_count`` is still the clean file's.
    """
    tier = tier_file or real_file(day, schema)
    definition = definition_file or real_file(day, TickSchema.DEFINITION)
    job_dir = write_job_dir(
        archive_root,
        job_id,
        {
            tier.name: _tier_bytes(tier, bad_header),
            definition.name: definition.read_bytes(),
        },
    )
    files = {path.name: _job_file(job_dir / path.name) for path in (tier, definition)}
    count = record_count(tier)
    with psycopg.connect(url, autocommit=True) as conn:
        definition_unit = _unit(
            conn, TickSchema.DEFINITION, day, files[definition.name], archive_root
        )
        unit = _unit(
            conn,
            schema,
            day,
            files[tier.name],
            archive_root,
            provider_record_count=(
                count if provider_record_count is None else provider_record_count
            ),
        )
    clock = FakeClock(SEEDED_AT)
    async with connect(url) as aconn:
        run = tick_run(aconn, FakeTickProvider(clock=clock), archive_root, clock)
        tally = await project_definitions(run, READER)
    assert tally.failed == 0, f"definition projection failed: {tally.to_dict()}"
    return SeededUnit(unit, definition_unit, files[tier.name], count)
