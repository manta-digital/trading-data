"""A delivered job's files → archive → units (224, split from ``in_flight``).

LLD 224 *in_flight.advance()*: download the job into ``<archive>/<job_id>``
(free, verified, resumable), match each ``.dbn.zst`` file to its unit by its
header (never by parsing the name), record it *downloaded*, record a delivered
day with no file as ``PROVIDER_HOLE``, and write ``manifest.json`` if the job
did not deliver one (so the purchase can be re-adopted, TD10).

Failures: a ``ProviderError`` from the download is a transient attempt on every
delivered unit of the job and propagates (the phase aborts, so a unit is
charged at most one attempt per pass); an ``OSError`` is a host fault, raised as
:class:`TickArchiveWriteError` with no attempt counted.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from manta_trading.data.quality.fetch_status import OPEN_FETCH_STATUSES
from manta_trading.data.tick.adopt_files import (
    MANIFEST_NAME,
    PARTIAL_SUFFIX,
    TickArchiveWriteError,
)
from manta_trading.data.tick.constants import UnitState
from manta_trading.data.tick.hashing import sha256_file
from manta_trading.data.tick.manifest_reads import (
    RequestRow,
    UnitFile,
    UnitRow,
    units_of_request,
)
from manta_trading.data.tick.manifest_repo import (
    mark_downloaded,
    mark_provider_hole,
    record_failure,
)
from manta_trading.data.tick.provider import ITickFileReader
from manta_trading.data.tick.run_context import TickRun
from manta_trading.logging import get_logger
from manta_trading.providers.errors import ProviderError

logger = get_logger(__name__)

#: Provider data-file suffixes; every other delivered file is job metadata.
DATA_FILE_SUFFIXES = (".dbn.zst", ".dbn")
_HASH_PREFIX = "sha256:"


@dataclass
class DeliveryTally:
    """What one job's delivery did."""

    downloaded: int = 0
    holed: int = 0
    strays: list[str] = field(default_factory=list)


def _delivered(units: list[UnitRow]) -> dict[date, UnitRow]:
    """The units still waiting for their file, by day."""
    return {
        unit.unit_date: unit
        for unit in units
        if unit.state is UnitState.DELIVERED
        and unit.reopened_at is None
        and unit.fetch_status in OPEN_FETCH_STATUSES
    }


def _headed_files(paths: tuple[Path, ...], reader: ITickFileReader) -> dict[date, Path]:
    """Data files by the UTC day their header starts on. Blocking."""
    by_day: dict[date, Path] = {}
    for path in paths:
        if not path.name.endswith(DATA_FILE_SUFFIXES):
            continue
        day = reader.open_file(path).start.date()
        if day in by_day:
            raise ValueError(f"{path.name} and {by_day[day].name} both start on {day}")
        by_day[day] = path
    return by_day


def _write_manifest(job_dir: Path, job_id: str) -> bool:
    """``manifest.json`` listing every other file, unless one exists. Blocking."""
    target = job_dir / MANIFEST_NAME
    if target.exists():
        return False
    listed = [
        {
            "filename": path.name,
            "size": path.stat().st_size,
            "hash": f"{_HASH_PREFIX}{sha256_file(path)}",
        }
        for path in sorted(job_dir.iterdir())
        if path.is_file() and not path.name.endswith(PARTIAL_SUFFIX)
    ]
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    partial.write_text(json.dumps({"job_id": job_id, "files": listed}, indent=2))
    partial.rename(target)
    return True


async def _download(
    run: TickRun, job_id: str, job_dir: Path, waiting: dict[date, UnitRow]
) -> tuple[Path, ...]:
    """The job's files. A provider failure counts one attempt on every waiting
    unit and propagates; an ``OSError`` is a host fault with no attempt."""
    try:
        return await asyncio.to_thread(run.provider.download_batch, job_id, job_dir)
    except ProviderError as exc:
        for unit in waiting.values():
            await record_failure(
                run.conn,
                unit.unit_id,
                UnitState.DELIVERED,
                f"download failed: {exc}",
                run.clock(),
                deterministic=False,
            )
        raise
    except OSError as exc:
        raise TickArchiveWriteError(job_dir, exc) from exc


def _unit_file(job_id: str, path: Path) -> UnitFile:
    """The archive-relative path, size and SHA-256 of a downloaded file. Blocking."""
    return UnitFile(f"{job_id}/{path.name}", path.stat().st_size, sha256_file(path))


async def _record_file(run: TickRun, job_id: str, unit: UnitRow, path: Path) -> None:
    file = await asyncio.to_thread(_unit_file, job_id, path)
    await mark_downloaded(run.conn, unit.unit_id, file, run.clock())


async def deliver_job(
    run: TickRun, request: RequestRow, reader: ITickFileReader
) -> DeliveryTally:
    """Download one delivered job and advance its units; see the module docstring."""
    job_id = request.job_id
    assert job_id is not None, "only requests with a job id are delivered"
    units = await units_of_request(run.conn, request.request_id)
    waiting = _delivered(units)
    job_dir = run.archive_root / job_id
    paths = await _download(run, job_id, job_dir, waiting)
    try:
        by_day = await asyncio.to_thread(_headed_files, paths, reader)
        wrote = await asyncio.to_thread(_write_manifest, job_dir, job_id)
    except OSError as exc:
        raise TickArchiveWriteError(job_dir, exc) from exc
    if wrote:
        logger.info("tick job %s: wrote %s (none delivered)", job_id, MANIFEST_NAME)
    tally = DeliveryTally()
    known_days = {unit.unit_date for unit in units}
    for day, path in sorted(by_day.items()):
        if day in waiting:
            await _record_file(run, job_id, waiting[day], path)
            tally.downloaded += 1
        elif day not in known_days:
            tally.strays.append(path.name)
    for day, unit in waiting.items():
        if day not in by_day:
            await mark_provider_hole(run.conn, unit.unit_id, run.clock())
            tally.holed += 1
    return tally
