"""``build_status`` and ``build_coverage`` after real ingests (slice 225, FR9, FR10).

Two real days are ingested (2024-09-03 trades, 2024-12-03 tbbo). With no tier
set, the scope is the sessions those days touch. A unit with no rows is added
for 2024-09-02 so session 09-03 has every day ingested; that unit also brings
session 09-02 into scope. Conditions give the sessions different buckets.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.ingest import ingest_inputs, run_ingest_phase
from tick_support.rows import (
    FILE_COLUMNS,
    insert_dataset_edge,
    insert_day_condition,
    insert_request,
    insert_unit,
)
from tick_support.runs import connect
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit

from manta_trading.data.tick.constants import DatasetCondition, TickSchema, UnitState
from manta_trading.data.tick.tick_coverage import build_coverage
from manta_trading.data.tick.tick_status_build import build_status
from manta_trading.data.tick.universe import TICK_UNIVERSE

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
OBSERVED = datetime(2026, 9, 30, 11, tzinfo=UTC)
ES = TICK_UNIVERSE[0]
AConn = psycopg.AsyncConnection[Any]


@pytest.fixture
async def ingested(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> AsyncIterator[AConn]:
    url = migrated_tick_db
    await seed_tier_unit(url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T")
    await seed_tier_unit(url, tmp_path, TickSchema.TBBO, TBBO_DAY, job_id="B")
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db)
    )
    assert report.summary["ingested"] == 2
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(conn, range_start=date(2024, 9, 2))
        insert_unit(
            conn,
            request,
            unit_date=date(2024, 9, 2),
            state=UnitState.INGESTED.value,
            **FILE_COLUMNS,
        )
        insert_dataset_edge(conn, observed_at=OBSERVED)
        for day, condition in (
            (date(2024, 9, 2), DatasetCondition.AVAILABLE),
            (date(2024, 9, 3), DatasetCondition.DEGRADED),
            (date(2024, 9, 4), DatasetCondition.AVAILABLE),
            (date(2024, 12, 2), DatasetCondition.PENDING),
            (date(2024, 12, 3), DatasetCondition.AVAILABLE),
        ):
            insert_day_condition(conn, condition_date=day, condition=condition.value)
    async with connect(url) as conn:
        yield conn


async def test_status_buckets_contracts_and_json(
    ingested: AConn, session_migrated_db: str
) -> None:
    status = await build_status(ingested, session_migrated_db, TICK_UNIVERSE, NOW)
    (es,) = status.products
    assert {k: v for k, v in es.buckets.items() if v} == {
        "complete": 1,  # 09-03: 09-02 and 09-03 both ingested
        "missing": 1,  # 09-04: 09-04 has no unit, condition available
        "pending": 1,  # 12-03: 12-02 has no unit, condition pending
        # 09-02 (opens Sunday 09-01, which has no row) and 12-04 (no row)
        "edge_unknown": 2,
    }
    # Day 09-03 (degraded) is in sessions 09-03 and 09-04: both are degraded.
    assert (es.sessions_held, es.degraded, es.caught_up) == (5, 2, None)
    assert es.edge_age_seconds == 3600
    assert es.contracts and all(c.instrument_class == "F" for c in es.contracts)
    assert es.spreads_hidden > 0
    body = status.to_dict()
    assert body["complete_basis"] == "units"
    assert json.loads(json.dumps(body)) == body
    every = await build_status(
        ingested, session_migrated_db, TICK_UNIVERSE, NOW, all_instruments=True
    )
    assert len(every.products[0].contracts) == len(es.contracts) + es.spreads_hidden


async def test_coverage_is_ok_then_names_a_deleted_row(
    ingested: AConn, session_migrated_db: str
) -> None:
    start, end = date(2024, 9, 3), date(2024, 9, 5)
    coverage = await build_coverage(ingested, session_migrated_db, ES, start, end)
    assert [(s.session_date, s.check) for s in coverage.sessions] == [
        (date(2024, 9, 3), "ok"),
        (date(2024, 9, 4), "ok"),
    ]
    assert coverage.sessions[0].ledger == coverage.sessions[0].raw > 0
    # By primary key: ``ctid`` is per chunk on a hypertable, so a ctid match
    # deletes one row in every chunk.
    deleted = await ingested.execute(
        "DELETE FROM tick_trade WHERE (instrument_id, ts_event, sequence,"
        " sequence_ordinal) = (SELECT instrument_id, ts_event, sequence,"
        " sequence_ordinal FROM tick_trade WHERE instrument_id = 46995"
        " ORDER BY ts_event LIMIT 1)"
    )
    assert deleted.rowcount == 1
    after = await build_coverage(ingested, session_migrated_db, ES, start, end)
    assert after.mismatched
    (bad,) = [s for s in after.sessions if s.mismatches]
    (mismatch,) = bad.mismatches
    assert mismatch.instrument_id == 46995
    assert mismatch.ledger == mismatch.raw + 1
    body = after.to_dict()
    assert body["mismatched"] is True
    assert json.loads(json.dumps(body)) == body
