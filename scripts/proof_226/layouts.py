"""``layouts``: compress under layout A, then B; measure; decide (LLD 226 TD4).

Each layout: set it, compress every chunk, read compressed bytes per row by
tier, run the query set, time the shipped supersession delete on a
compressed chunk (inside a transaction that is rolled back), decompress. The
step begins by decompressing everything, so a re-run is safe. Every
statement that changes the table runs behind the proof-database guard.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from manta_trading.config import Settings
from manta_trading.data.tick.constants import UnitState
from manta_trading.data.tick.ingest_plan import Calendars, SupersededUnit, build_plan
from manta_trading.data.tick.ingest_worker import supersede
from manta_trading.data.tick.manifest_reads import units_by_id
from manta_trading.data.tick.tick_calendar import calendar_url
from proof_226 import queries, size
from proof_226.common import ProofUrls, Report, load_proof_urls
from proof_226.guard import SyncConn, destructive


@dataclass(frozen=True)
class Layout:
    name: str
    segment_by: tuple[str, ...]
    order_by: tuple[str, ...]


LAYOUT_A = Layout("A", ("instrument_id",), ("ts_event", "sequence", "sequence_ordinal"))
LAYOUT_B = Layout(
    "B", (), ("instrument_id", "ts_event", "sequence", "sequence_ordinal")
)
#: TD4: within this fraction on both bytes and Q1–Q4, choose A.
TIE_FRACTION = 0.10


@dataclass(frozen=True)
class LayoutResult:
    layout: Layout
    rejected: str | None
    bytes_per_row: dict[str, float]
    overall_bytes_per_row: float
    results: list[queries.QueryResult]
    delete_seconds: float | None

    @property
    def meets_bounds(self) -> bool:
        return all(r.within_bounds for r in self.results)

    @property
    def worst_bounded_ms(self) -> float:
        return max(r.execution_ms for r in self.results if r.query in queries.BOUNDED)


@destructive
def decompress_all(conn: SyncConn) -> None:
    conn.execute(
        "SELECT decompress_chunk(c, if_compressed => true)"
        " FROM show_chunks('tick_trade') AS c"
    )


@destructive
def set_layout(conn: SyncConn, layout: Layout) -> None:
    conn.execute(
        "ALTER TABLE tick_trade SET (timescaledb.enable_columnstore,"
        f" timescaledb.segmentby = '{', '.join(layout.segment_by)}',"
        f" timescaledb.orderby = '{', '.join(layout.order_by)}')"
    )


@destructive
def compress_all(conn: SyncConn) -> None:
    conn.execute(
        "SELECT compress_chunk(c, if_not_compressed => true)"
        " FROM show_chunks('tick_trade') AS c"
    )


_COMPRESSED_BYTES = """
SELECT format('%I.%I', chunk_schema, chunk_name)::regclass::text
     , after_compression_total_bytes
  FROM chunk_columnstore_stats('tick_trade')
 WHERE compression_status = 'Compressed'
"""


def compressed_bytes_per_row(
    conn: psycopg.Connection[Any],
) -> tuple[dict[str, float], float]:
    """Compressed bytes per row by tier, and over the whole table."""
    rows_by_chunk = size.query_rows(conn, size.ROWS_BY_CHUNK)
    after = {c: int(b) for c, b in size.query_rows(conn, _COMPRESSED_BYTES)}
    rows, table, _ = size.tier_rows_and_bytes(rows_by_chunk, after)
    by_tier = {tier: table[tier] / rows[tier] for tier in sorted(table)}
    return by_tier, sum(after.values()) / sum(rows.values())


async def _plan_with_victim(
    db_url: str, calendars_url: str, unit_id: int, victim: SupersededUnit
) -> Any:
    from manta_trading.data.tick.store_context import connect_migrated

    conn = await connect_migrated(db_url)
    try:
        (unit,) = await units_by_id(conn, [unit_id])
        calendars = Calendars(calendars_url)
        try:
            plan = await build_plan(conn, calendars, unit)
        finally:
            calendars.close()
    finally:
        await conn.close()
    return dataclasses.replace(plan, superseded=(victim,))


_VICTIM = """
SELECT unit.unit_id, min(ledger.first_event_ns), max(ledger.last_event_ns)
  FROM tick_archive_unit AS unit
  JOIN tick_ingest_ledger AS ledger USING (unit_id)
 WHERE unit.state = %s AND unit.superseded_by_unit_id IS NULL
 GROUP BY unit.unit_id
 ORDER BY sum(ledger.record_count) DESC
 LIMIT 2
"""


def time_supersession_delete(db_url: str, calendars_url: str) -> float:
    """The shipped ``supersede`` deleting the largest unit's rows from
    compressed chunks, inside a transaction rolled back afterwards."""
    with psycopg.connect(db_url) as conn:
        rows = conn.execute(_VICTIM, (UnitState.INGESTED.value,)).fetchall()
    (victim_id, first, last), (other_id, _, _) = rows
    victim = SupersededUnit(victim_id, UnitState.INGESTED, first, last)
    plan = asyncio.run(_plan_with_victim(db_url, calendars_url, other_id, victim))
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        started = time.monotonic()
        supersede(cur, plan)
        took = time.monotonic() - started
        conn.rollback()
    return took


def measure_layout(
    urls: ProofUrls,
    calendars_url: str,
    layout: Layout,
    targets: list[queries.Target],
) -> LayoutResult:
    maint_url, db_url = urls.maintenance_url, urls.db_url
    with psycopg.connect(maint_url, autocommit=True) as conn:
        decompress_all(conn)
        try:
            set_layout(conn, layout)
        except psycopg.Error as exc:  # TD4: a refused layout is a result
            return LayoutResult(layout, str(exc).strip(), {}, 0.0, [], None)
        compress_all(conn)
        per_tier, overall = compressed_bytes_per_row(conn)
    with psycopg.connect(db_url) as conn:
        results = queries.run_query_set(conn, targets)
    delete = time_supersession_delete(db_url, calendars_url)
    with psycopg.connect(maint_url, autocommit=True) as conn:
        decompress_all(conn)
    return LayoutResult(layout, None, per_tier, overall, results, delete)


def decide(a: LayoutResult, b: LayoutResult) -> str:
    """TD4: lower bytes wins unless it misses a Q1–Q4 bound the other meets;
    within 10 % on both bytes and Q1–Q4 time, A."""
    usable = [r for r in (a, b) if r.rejected is None]
    if len(usable) == 1:
        return f"{usable[0].layout.name}: the other layout was refused by TimescaleDB"
    if not usable:
        return "neither: both layouts were refused (stop and tell the PM)"
    close_bytes = (
        abs(a.overall_bytes_per_row - b.overall_bytes_per_row)
    ) / a.overall_bytes_per_row <= TIE_FRACTION
    close_time = abs(a.worst_bounded_ms - b.worst_bounded_ms) <= (
        TIE_FRACTION * a.worst_bounded_ms
    )
    if close_bytes and close_time:
        return "A: within 10 % of B on bytes per row and Q1–Q4 time"
    smaller, other = (
        (a, b) if a.overall_bytes_per_row <= b.overall_bytes_per_row else (b, a)
    )
    if not smaller.meets_bounds and other.meets_bounds:
        return (
            f"{other.layout.name}: {smaller.layout.name} is smaller but misses "
            "a Q1–Q4 bound"
        )
    return f"{smaller.layout.name}: lower compressed bytes per row"


def run() -> Path:
    urls = load_proof_urls()
    report = Report("layouts", "Proof: compression layouts A and B (slice 226)")
    with psycopg.connect(urls.db_url) as conn:
        targets = queries.pick_targets(conn)
    measured = [
        measure_layout(urls, calendar_url(Settings()), layout, targets)
        for layout in (LAYOUT_A, LAYOUT_B)
    ]
    for result in measured:
        _write_layout(report, result)
    report.add("## Decision (TD4)", "", f"**{decide(*measured)}**")
    return report.write()


def _write_layout(report: Report, result: LayoutResult) -> None:
    layout = result.layout
    report.add(
        f"## Layout {layout.name}",
        "",
        f"segmentby `{', '.join(layout.segment_by) or '(none)'}`, "
        f"orderby `{', '.join(layout.order_by)}`",
        "",
    )
    if result.rejected:
        report.add(f"Refused by TimescaleDB: `{result.rejected}`", "")
        return
    report.table(
        ("tier", "compressed B/row"),
        [(t, f"{v:.2f}") for t, v in result.bytes_per_row.items()]
        + [("whole table", f"{result.overall_bytes_per_row:.2f}")],
    )
    queries.write_results(report, result.results)
    report.add(
        f"Supersession delete on compressed chunks (input to the lock-timeout "
        f"rule, 9.1): **{result.delete_seconds:.2f} s**",
        "",
    )
