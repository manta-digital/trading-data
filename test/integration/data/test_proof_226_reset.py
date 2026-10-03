"""The proof harness reset on a fixture's throwaway tick database (226 TD2).

The guard compares ``current_database()`` with ``TICK_PROOF_DB_NAME``; the
fixture's database has its own generated name, so the test points the guard's
name at it. The guard's refusal is the unit test's subject.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest
from tick_support.ingest import (
    ingest_inputs,
    run_ingest_phase,
    scalar,
    trade_count,
    unit_state,
)
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.pass_contract import TickOutcome

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from proof_226 import guard  # noqa: E402


async def test_reset_empties_ticks_and_returns_tier_units_to_verified(
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = migrated_tick_db
    trades = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )
    tbbo = await seed_tier_unit(url, tmp_path, TickSchema.TBBO, TBBO_DAY, job_id="B")
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db)
    )
    assert report.outcome is TickOutcome.OK
    assert trade_count(url, trades.unit_id) > 0

    name = scalar(url, "SELECT current_database()")
    monkeypatch.setattr(guard, "TICK_PROOF_DB_NAME", name)
    with psycopg.connect(url, autocommit=True) as conn:
        reset = guard.reset_proof_database(conn)

    assert reset == 2
    assert scalar(url, "SELECT count(*) FROM tick_trade") == 0
    assert scalar(url, "SELECT count(*) FROM tick_ingest_ledger") == 0
    for seeded in (trades, tbbo):
        assert unit_state(url, seeded.unit_id) == ("verified", "UNKNOWN", None, None)
        assert unit_state(url, seeded.definition_unit_id)[0] == "ingested"

    again = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db)
    )
    assert again.summary["ingested"] == 2, "a reset unit is selectable again"
