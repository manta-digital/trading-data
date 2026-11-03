"""Record a definition unit and a bar unit for bar-ingest tests (slice 302).

Both are units of the same ``ES.FUT`` parent request shape: the definition unit
is *ingested* by 224's own projection (``project_definitions``, the call the
acquisition pass makes; never a direct status update), the bar unit is
*verified* and waits for ``mt data tick ingest``. The calendar side is the
session-scoped primary database, migrated through 301, so sessions reach back
to 2010. Files default to the committed real 2024-12-03 slices; a test that
needs other content writes a file with :func:`write_bar_file` and passes it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from tick_support.dbn_files import bar_file_bytes
from tick_support.tier_units import TBBO_DAY, SeededUnit, seed_tier_unit

from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema


def write_bar_file(path: Path, day: date, bars: Sequence[Mapping[str, Any]]) -> Path:
    """Write hand-built ``ohlcv-1m`` bars as a DBN day file at ``path``."""
    path.write_bytes(bar_file_bytes(CME_DATASET, day, SType.PARENT, bars))
    return path


async def seed_bar_units(
    tick_url: str,
    archive_root: Path,
    *,
    job_id: str,
    day: date = TBBO_DAY,
    bar_file: Path | None = None,
    definition_file: Path | None = None,
    require_definitions: bool = True,
) -> SeededUnit:
    """A verified ``ohlcv-1m`` unit for ``day`` and its ingested definition unit."""
    return await seed_tier_unit(
        tick_url,
        archive_root,
        TickSchema.OHLCV_1M,
        day,
        job_id=job_id,
        tier_file=bar_file,
        definition_file=definition_file,
        require_definitions=require_definitions,
    )
