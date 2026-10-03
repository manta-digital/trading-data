"""Load test for query latency on compressed tick data (slice 226, TD3).

The realistic configuration is the largest day of each tier the archive
holds, as the proof's ``batch`` step found them: 2024-12-18 ``tbbo``
(889,373 records) and 2024-09-18 ``trades`` (756,899). Each is read from
``/data/tick-archive``, ingested through the real pass into a fixture
database migrated through the newest tick migration (so ``tick_007``'s
columnstore layout applies), its chunk compressed explicitly (the test
cluster has no scheduler), and Q1–Q4 of the proof's query set run on it. If
an archived file is absent the test **fails** naming the path; it never
skips.

Bound: each of Q1–Q4 executes in at most 1 s warm (TD3). On the production
tick database, after the full rebuild, the worst was 258 ms
(``user/notes/2026-10-03-226-proof-final.md``).

Gate (CI runs no test job; see slice 907 — this gate is the load tier's gate,
as for every other load test):

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_226_tick_query_nfr.py

Requires ``MT_TIMESCALE_TEST_URL``. This tier never reads the production DB
URL; ``test_load_tier_never_references_prod_db_url`` enforces that.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import psycopg
import pytest
from tick_support.ingest import ingest_inputs, run_ingest_phase
from tick_support.tier_units import seed_tier_unit

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.pass_contract import TickOutcome

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from proof_226 import queries  # noqa: E402

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(900),
]

ARCHIVE = Path("/data/tick-archive")
#: tier → (day, tier file, definition file, records).
DAYS = {
    TickSchema.TRADES: (
        date(2024, 9, 18),
        ARCHIVE / "GLBX-20240930-USM7UXXJBA" / "glbx-mdp3-20240918.trades.dbn.zst",
        ARCHIVE / "GLBX-20260930-DLDYL5DM8Q" / "glbx-mdp3-20240918.definition.dbn.zst",
        756_899,
    ),
    TickSchema.TBBO: (
        date(2024, 12, 18),
        ARCHIVE / "GLBX-20250123-XT4GD5UM6C" / "glbx-mdp3-20241218.tbbo.dbn.zst",
        ARCHIVE / "GLBX-20260930-HVGRLYKHRN" / "glbx-mdp3-20241218.definition.dbn.zst",
        889_373,
    ),
}


async def test_q1_to_q4_on_compressed_chunks_within_a_second(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    for schema, (day, tier_file, definition_file, records) in DAYS.items():
        for path in (tier_file, definition_file):
            assert path.is_file(), f"archived day missing: {path}"
        seeded = await seed_tier_unit(
            url,
            tmp_path,
            schema,
            day,
            job_id=tier_file.parent.name,
            tier_file=tier_file,
            definition_file=definition_file,
        )
        assert seeded.record_count == records
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db)
    )
    assert report.outcome is TickOutcome.OK, report.error
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT compress_chunk(c) FROM show_chunks('tick_trade') AS c")
        targets = queries.pick_targets(conn)
        results = queries.run_query_set(conn, targets)
    bounded = [r for r in results if r.query in queries.BOUNDED]
    for r in bounded:
        print(f"\n{r.tier} {r.query}: {r.execution_ms:.1f} ms, {r.rows} rows")
    assert len(bounded) == len(DAYS) * len(queries.BOUNDED)
    slow = [r for r in bounded if r.execution_ms > queries.EXECUTION_BOUND_MS]
    assert slow == [], f"over {queries.EXECUTION_BOUND_MS} ms: {slow}"
