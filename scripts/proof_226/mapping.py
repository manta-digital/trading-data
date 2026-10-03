"""``mapping``: every record's instrument resolves through its file's header
mappings on the file's day (LLD 226 TD3).

The rule: 100.00 % of the current tier records, or NO-GO with the
``stype_in=raw_symbol`` fallback named. Read-only: the proof database
supplies the unit list, the archive supplies the bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import psycopg

from manta_trading.data.tick.constants import STORED_TIERS, UnitState
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.provider import SymbolInterval
from proof_226.common import (
    EXPECTED_ROWS,
    Report,
    archive_root,
    load_proof_urls,
)

FALLBACK = "stype_in=raw_symbol"
#: Unmapped instrument ids listed per file in the report, at most.
LISTED_MISSES = 10


@dataclass(frozen=True)
class FileMapping:
    unit_id: int
    day: date
    path: Path
    records: int
    misses: int
    missing_ids: tuple[int, ...]


def ids_on(mappings: Mapping[str, Sequence[SymbolInterval]], day: date) -> set[int]:
    """Instrument ids some header interval maps on ``day`` (``[start, end)``)."""
    return {
        interval.instrument_id
        for intervals in mappings.values()
        for interval in intervals
        if interval.start_date <= day < interval.end_date
    }


def check_file(unit_id: int, day: date, path: Path) -> FileMapping:
    tick_file = DbnFileReader().open_file(path)
    mapped = np.fromiter(ids_on(tick_file.mappings, day), dtype=np.uint32)
    records = misses = 0
    missing: set[int] = set()
    for batch in tick_file.iter_batches():
        ids = batch.records["instrument_id"]
        unmapped = ~np.isin(ids, mapped)
        records += batch.count
        misses += int(unmapped.sum())
        missing.update(int(i) for i in np.unique(ids[unmapped]))
    return FileMapping(
        unit_id, day, path, records, misses, tuple(sorted(missing))[:LISTED_MISSES]
    )


def current_tier_files(db_url: str, root: Path) -> list[tuple[int, date, Path]]:
    """Every current, ingested tier unit's file: what ``tick_trade`` holds."""
    with psycopg.connect(db_url) as conn:
        rows = conn.execute(
            """
            SELECT unit.unit_id
                 , unit.unit_date
                 , unit.file_path
              FROM tick_archive_unit AS unit
              JOIN tick_request AS request USING (request_id)
             WHERE request.schema = ANY(%s)
               AND unit.state = %s
               AND unit.superseded_by_unit_id IS NULL
               AND unit.reopened_at IS NULL
             ORDER BY unit.unit_date, unit.unit_id
            """,
            (sorted(t.value for t in STORED_TIERS), UnitState.INGESTED.value),
        ).fetchall()
    return [(int(uid), day, root / str(rel)) for uid, day, rel in rows]


def run() -> Path:
    urls = load_proof_urls()
    report = Report("mapping", "Proof: instrument mapping completeness (slice 226)")
    results = [
        check_file(uid, day, path)
        for uid, day, path in current_tier_files(urls.db_url, archive_root())
    ]
    checked = sum(r.records for r in results)
    missed = sum(r.misses for r in results)
    share = 100 * (checked - missed) / checked if checked else 0.0
    complete = missed == 0 and checked == EXPECTED_ROWS
    report.add(
        "## Result",
        "",
        f"- files checked: {len(results)}",
        f"- records checked: {checked:,} (expected {EXPECTED_ROWS:,})",
        f"- records whose instrument_id no header interval maps on the file's day: "
        f"{missed:,}",
        f"- mapped: **{share:.2f} %**",
        "",
        f"**Verdict: {'GO' if complete else 'NO-GO'}.**"
        + ("" if complete else f" Fallback: re-request with `{FALLBACK}`."),
        "",
    )
    misses = [r for r in results if r.misses]
    if misses:
        report.table(
            ("unit", "day", "records", "misses", f"first {LISTED_MISSES} ids"),
            [
                (r.unit_id, r.day, r.records, r.misses, list(r.missing_ids))
                for r in misses
            ],
        )
    return report.write()
