"""One archive unit's verification: *downloaded* → *verified* (223).

LLD 224 Technical Decision 9 (Verification):

- the file exists under its final name, and its size and SHA-256 — re-hashed,
  so a file changed on disk is caught — equal the unit's recorded values;
- its header's dataset, schema and ``stype_in`` equal the request's, and its
  start and end are exactly the unit's UTC day;
- the provider's free ``record_count`` for that day and the request's symbols
  is stored as ``provider_record_count``.

A mismatch is a deterministic unit failure naming the field (retrying reads
the same bytes). A ``ProviderError`` from the count call propagates: it is a
run-level failure for the caller's phase. Decoding every record is 225's
count check, not this step. Hashing and the header read run in a thread.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from manta_trading.data.tick.hashing import sha256_file
from manta_trading.data.tick.manifest_reads import UnitRow
from manta_trading.data.tick.manifest_repo import mark_verified, record_failure
from manta_trading.data.tick.provider import ITickFileReader, TickRequest
from manta_trading.data.tick.run_context import TickRun


@dataclass(frozen=True)
class VerifyOutcome:
    """``failure`` is ``None`` when the unit reached *verified*."""

    unit_id: int
    failure: str | None


def _day_bounds(unit: UnitRow) -> tuple[datetime, datetime]:
    start = datetime.combine(unit.unit_date, time(), UTC)
    return start, start + timedelta(days=1)


def _file_mismatch(path: Path, unit: UnitRow, reader: ITickFileReader) -> str | None:
    """The first field that does not match, or ``None``. Blocking."""
    assert unit.file is not None  # every state verify runs on holds a file
    if not path.is_file():
        return f"file {unit.file.path} is missing"
    size = path.stat().st_size
    if size != unit.file.size:
        return f"file size is {size}, recorded {unit.file.size}"
    if sha256_file(path) != unit.file.sha256:
        return "file SHA-256 differs from the recorded hash"
    header = reader.open_file(path)
    start, end = _day_bounds(unit)
    observed = {
        "dataset": (header.dataset, unit.dataset),
        "schema": (header.schema, unit.schema),
        "stype_in": (header.stype_in, unit.stype_in),
        "start": (header.start, start),
        "end": (header.end, end),
    }
    for field, (got, want) in observed.items():
        if got != want:
            return f"header {field} is {got}, expected {want}"
    return None


def _day_request(unit: UnitRow) -> TickRequest:
    return TickRequest(
        dataset=unit.dataset,
        symbols=unit.symbols,
        stype_in=unit.stype_in,
        schema=unit.schema,
        start=unit.unit_date,
        end=unit.unit_date + timedelta(days=1),
    )


async def check(run: TickRun, unit: UnitRow, reader: ITickFileReader) -> VerifyOutcome:
    """Verify one *downloaded* unit and record the result on the manifest."""
    assert unit.file is not None, f"unit {unit.unit_id} has no file to verify"
    path = run.archive_root / unit.file.path
    mismatch = await asyncio.to_thread(_file_mismatch, path, unit, reader)
    if mismatch is not None:
        await record_failure(
            run.conn,
            unit.unit_id,
            unit.state,
            mismatch,
            run.clock(),
            deterministic=True,
        )
        return VerifyOutcome(unit.unit_id, mismatch)
    count = await asyncio.to_thread(run.provider.record_count, _day_request(unit))
    await mark_verified(run.conn, unit.unit_id, count, run.clock())
    return VerifyOutcome(unit.unit_id, None)
