"""``size``: bytes per record per tier, instrument skew, spread share (TD3, TD4).

Every ratio divides by an exact ``count(*)``. The tiers occupy separate
chunks, so per-chunk sizes give per-tier table bytes; a chunk holding both
tiers is reported, not apportioned. Spread rows are matched to their
definition through its validity window (no two windows of one id overlap,
222's exclusion constraint); spread bytes are the spread row share of the
tier's bytes, since a tier's rows are fixed width. Read-only.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import databento_dbn
import psycopg

from manta_trading.data.tick.constants import STORED_TIERS
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from proof_226.common import Report, archive_root, load_proof_urls
from proof_226.mapping import current_tier_files

#: Databento's spread instrument classes (``databento_dbn.InstrumentClass``;
#: ``str`` of a member is its one-letter code, as ``tick_definition`` stores it).
SPREAD_CLASSES = tuple(
    str(c)
    for c in (
        databento_dbn.InstrumentClass.FUTURE_SPREAD,
        databento_dbn.InstrumentClass.MIXED_SPREAD,
        databento_dbn.InstrumentClass.OPTION_SPREAD,
    )
)
_TIERS = sorted(t.value for t in STORED_TIERS)

_ROWS_BY_CHUNK = """
SELECT trade.tableoid::regclass::text AS chunk
     , request.schema
     , count(*)
  FROM tick_trade AS trade
  JOIN tick_archive_unit AS unit USING (unit_id)
  JOIN tick_request AS request USING (request_id)
 GROUP BY 1, 2
"""
_CHUNK_BYTES = """
SELECT format('%I.%I', chunk_schema, chunk_name)::regclass::text
     , table_bytes + index_bytes + toast_bytes
  FROM chunks_detailed_size('tick_trade')
"""
_INSTRUMENT_ROWS = """
SELECT tableoid::regclass::text, instrument_id, count(*)
  FROM tick_trade GROUP BY 1, 2
"""
_CLASS_ROWS = """
SELECT request.schema
     , coalesce(definition.instrument_class, '(none)')
     , count(*)
  FROM tick_trade AS trade
  JOIN tick_archive_unit AS unit USING (unit_id)
  JOIN tick_request AS request USING (request_id)
  LEFT JOIN tick_definition AS definition
    ON definition.instrument_id = trade.instrument_id
   AND trade.ts_event >= definition.activation_ns
   AND trade.ts_event < definition.expiration_ns
 GROUP BY 1, 2
"""


def _query(conn: psycopg.Connection[Any], sql: str) -> list[tuple[Any, ...]]:
    return conn.execute(sql).fetchall()  # type: ignore[arg-type]


def tier_rows_and_bytes(
    rows_by_chunk: list[tuple[str, str, int]], chunk_bytes: dict[str, int]
) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """Rows and table bytes per tier, and the chunks holding more than one tier."""
    tiers_in: dict[str, set[str]] = defaultdict(set)
    rows: dict[str, int] = defaultdict(int)
    for chunk, tier, count in rows_by_chunk:
        tiers_in[chunk].add(tier)
        rows[tier] += count
    mixed = sorted(c for c, tiers in tiers_in.items() if len(tiers) > 1)
    table: dict[str, int] = defaultdict(int)
    for chunk, tiers in tiers_in.items():
        if len(tiers) == 1:
            table[next(iter(tiers))] += chunk_bytes[chunk]
    return dict(rows), dict(table), mixed


def _archive(db_url: str) -> tuple[dict[str, int], dict[str, int]]:
    """Archive bytes and native DBN record size per tier, from the files."""
    sizes: dict[str, int] = defaultdict(int)
    record: dict[str, int] = {}
    reader = DbnFileReader()
    for _, _, path in current_tier_files(db_url, archive_root()):
        tick_file = reader.open_file(path)
        sizes[tick_file.schema.value] += path.stat().st_size
        record.setdefault(tick_file.schema.value, tick_file.record_size)
    return dict(sizes), record


def skew(instrument_rows: list[tuple[str, int, int]]) -> list[tuple[object, ...]]:
    """Per chunk: instruments, rows, top-1 and top-5 share, median rows."""
    by_chunk: dict[str, list[int]] = defaultdict(list)
    for chunk, _, count in instrument_rows:
        by_chunk[chunk].append(count)
    out: list[tuple[object, ...]] = []
    for chunk in sorted(by_chunk):
        counts = sorted(by_chunk[chunk], reverse=True)
        total = sum(counts)
        out.append(
            (
                chunk,
                len(counts),
                total,
                _pct(counts[0], total),
                _pct(sum(counts[:5]), total),
                int(median(counts)),
            )
        )
    return out


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "n/a"


def run() -> Path:
    urls = load_proof_urls()
    report = Report("size", "Proof: storage size, skew and spread share (slice 226)")
    with psycopg.connect(urls.db_url) as conn:
        rows_by_chunk = _query(conn, _ROWS_BY_CHUNK)
        chunk_bytes = {c: int(b) for c, b in _query(conn, _CHUNK_BYTES)}
        instrument_rows = _query(conn, _INSTRUMENT_ROWS)
        class_rows = _query(conn, _CLASS_ROWS)
    rows, table, mixed = tier_rows_and_bytes(rows_by_chunk, chunk_bytes)
    archive, record = _archive(urls.db_url)
    report.add("## Bytes per record (uncompressed table)", "")
    report.table(
        ("tier", "rows (exact)", "archive B/row", "DBN record B", "table B/row"),
        [
            (
                t,
                f"{rows.get(t, 0):,}",
                _per(archive.get(t), rows.get(t)),
                record.get(t, "n/a"),
                _per(table.get(t), rows.get(t)),
            )
            for t in _TIERS
        ],
    )
    if mixed:
        report.add(f"Chunks holding both tiers (not in table B/row): {mixed}", "")
    _write_spreads(report, class_rows, rows, table)
    report.add("## Rows per instrument per chunk (space-partitioning evidence)", "")
    report.table(
        ("chunk", "instruments", "rows", "top 1", "top 5", "median rows"),
        skew(instrument_rows),
    )
    return report.write()


def _per(total: int | None, count: int | None) -> str:
    return f"{total / count:.1f}" if total is not None and count else "n/a"


def _write_spreads(
    report: Report,
    class_rows: list[tuple[str, str, int]],
    rows: dict[str, int],
    table: dict[str, int],
) -> None:
    spread: dict[str, int] = defaultdict(int)
    unresolved: dict[str, int] = defaultdict(int)
    for tier, cls, count in class_rows:
        if cls in SPREAD_CLASSES:
            spread[tier] += count
        elif cls == "(none)":
            unresolved[tier] += count
    report.add(f"## Spread share (classes {', '.join(SPREAD_CLASSES)})", "")
    report.table(
        (
            "tier",
            "spread rows",
            "share of rows",
            "spread bytes (row share)",
            "rows with no definition",
        ),
        [
            (
                t,
                f"{spread[t]:,}",
                _pct(spread[t], rows.get(t, 0)),
                f"{spread[t] * table.get(t, 0) // rows[t]:,}" if rows.get(t) else "n/a",
                unresolved[t],
            )
            for t in _TIERS
        ],
    )
