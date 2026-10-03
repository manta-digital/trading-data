"""The proof harness's read-side SQL on real ingested slices (226 TD3).

Runs the query set and the ``size`` step's queries against a fixture's tick
database holding the two real slices, so a SQL error surfaces here rather
than on the production host.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from tick_support.ingest import ingest_inputs, run_ingest_phase
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.pass_contract import TickOutcome

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from proof_226 import queries, size  # noqa: E402
from proof_226.mapping import current_tier_files  # noqa: E402


async def _two_slices(url: str, archive: Path, calendar_url: str) -> int:
    trades = await seed_tier_unit(
        url, archive, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )
    tbbo = await seed_tier_unit(url, archive, TickSchema.TBBO, TBBO_DAY, job_id="B")
    report = await run_ingest_phase(url, archive, ingest_inputs(url, calendar_url))
    assert report.outcome is TickOutcome.OK
    return trades.record_count + tbbo.record_count


async def test_query_set_and_size_queries_run_on_real_slices(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    total = await _two_slices(url, tmp_path, session_migrated_db)
    with psycopg.connect(url) as conn:
        targets = queries.pick_targets(conn)
        results = queries.run_query_set(conn, targets)
        rows_by_chunk = size._query(conn, size._ROWS_BY_CHUNK)
        chunk_bytes = {c: int(b) for c, b in size._query(conn, size._CHUNK_BYTES)}
        class_rows = size._query(conn, size._CLASS_ROWS)
        instrument_rows = size._query(conn, size._INSTRUMENT_ROWS)

    assert [t.tier for t in targets] == ["tbbo", "trades"]
    assert len(results) == len(targets) * len(queries.QUERIES)
    by_query = {(r.tier, r.query): r for r in results}
    for target in targets:
        q1 = by_query[(target.tier, "Q1")]
        assert q1.rows == target.records, "Q1 returns the session's ledger count"
        assert by_query[(target.tier, "Q4")].rows == 1
        assert all(by_query[(target.tier, q)].execution_ms > 0 for q in queries.QUERIES)

    rows, table, mixed = size.tier_rows_and_bytes(rows_by_chunk, chunk_bytes)
    assert sum(rows.values()) == total
    assert set(table) == {"trades", "tbbo"} and mixed == []
    assert sum(count for _, _, count in class_rows) == total
    assert sum(count for _, _, count in instrument_rows) == total
    assert len(current_tier_files(url, tmp_path)) == 2
