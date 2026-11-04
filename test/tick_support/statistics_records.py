"""Hand-built ``statistics`` records in the reader's own layout (slice 303).

The layout is read from the DBN statistics samples, so a record built here is
what ``DbnFile`` yields: ``statistics_records`` for a unit test of a projector
and ``write_statistics_file`` for an integration test through the real reader.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import databento
import numpy as np
from tick_support.dbn_files import FIXTURES, statistics_file_bytes
from tick_support.tier_units import SeededUnit, seed_tier_unit

from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema
from manta_trading.data.tick.statistics_constants import StatType

V3_SAMPLE = "test_data.statistics.v3.dbn.zst"
V1_SAMPLE = "test_data.statistics.v1.dbn.zst"
UNDEF_TS_REF = 2**64 - 1
UNDEF_PRICE = 2**63 - 1
UNDEF_QUANTITY_V3 = 2**63 - 1
UNDEF_QUANTITY_V1 = 2**31 - 1
NEW, DELETE = 1, 2


def ns_of(day: date, hour: int = 0, minute: int = 0) -> int:
    """Epoch nanoseconds of ``day`` at ``hour``:``minute`` UTC."""
    instant = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    return int(instant.timestamp()) * 1_000_000_000


def stat(
    instrument_id: int,
    stat_type: int,
    *,
    ts_recv: int,
    ts_ref: int = UNDEF_TS_REF,
    sequence: int = 1,
    price: int = 100_000_000_000,
    quantity: int = 5,
    update_action: int = NEW,
    ts_event: int | None = None,
) -> dict[str, Any]:
    """One record's overrides; ``ts_event`` defaults to ``ts_recv``."""
    return {
        "instrument_id": instrument_id,
        "stat_type": int(stat_type),
        "ts_recv": ts_recv,
        "ts_event": ts_recv if ts_event is None else ts_event,
        "ts_ref": ts_ref,
        "sequence": sequence,
        "price": price,
        "quantity": quantity,
        "update_action": update_action,
        "stat_flags": 0,
    }


def referenced(
    instrument_id: int, stat_type: StatType, ref_day: date, **fields: Any
) -> dict[str, Any]:
    """A referenced statistic whose ``ts_ref`` is 16:00 UTC of ``ref_day``."""
    return stat(instrument_id, stat_type, ts_ref=ns_of(ref_day, 16), **fields)


def statistics_records(
    rows: Sequence[Mapping[str, Any]], sample: str = V3_SAMPLE
) -> np.ndarray[Any, Any]:
    """Records in the sample's layout (v3, or v1 with its narrower quantity)."""
    template = databento.DBNStore.from_file(FIXTURES / sample).to_ndarray()[0]
    records = np.zeros(len(rows), dtype=template.dtype)
    for index, overrides in enumerate(rows):
        records[index] = template
        for name, value in overrides.items():
            records[index][name] = value
    return records


def write_statistics_file(
    path: Path, day: date, rows: Sequence[Mapping[str, Any]]
) -> Path:
    """Write the rows as a DBN statistics day file at ``path``."""
    path.write_bytes(statistics_file_bytes(CME_DATASET, day, SType.PARENT, rows))
    return path


async def seed_statistics_units(
    tick_url: str,
    archive_root: Path,
    day: date,
    rows: Sequence[Mapping[str, Any]],
    *,
    job_id: str,
    file_name: str = "statistics.dbn.zst",
) -> SeededUnit:
    """A verified ``statistics`` unit holding ``rows`` and its ingested definitions.

    The file is written beside ``archive_root`` first, then laid out as a batch
    job; ``provider_record_count`` is the file's true count.
    """
    source = archive_root.parent / f"{job_id}-{file_name}"
    write_statistics_file(source, day, rows)
    return await seed_tier_unit(
        tick_url,
        archive_root,
        TickSchema.STATISTICS,
        day,
        job_id=job_id,
        tier_file=source,
    )
