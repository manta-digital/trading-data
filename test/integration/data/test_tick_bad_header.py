"""A bad DBN header fails its unit, not the pass (226 TD 8).

Verify runs through the in-flight phase's verify loop over two *downloaded*
units, one file with a rewritten header; ingest runs over two real slices,
one archived with a rewritten header; delivery over a fake provider whose
second day arrives with a rewritten header (adopt's case is in
``test_tick_adopt.py``).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from tick_support.dbn_files import JobFile, bad_header_bytes, day_file_bytes
from tick_support.fake_provider import FakeClock, FakeTickProvider, request_for
from tick_support.ingest import ingest_inputs, run_ingest_phase, scalar, unit_state
from tick_support.rows import insert_request, insert_unit
from tick_support.runs import connect, tick_run
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit

from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema, UnitState
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.in_flight import advance
from manta_trading.data.tick.manifest_pass import insert_pending_request, record_submit
from manta_trading.data.tick.manifest_reads import units_of_request
from manta_trading.data.tick.pass_contract import TickOutcome
from manta_trading.data.tick.planner import PlannedRequest

START = datetime(2026, 9, 29, 12, tzinfo=UTC)
DAYS = (date(2024, 9, 3), date(2024, 9, 4))
FIXTURE = "test_data.trades.v3.dbn.zst"


def write_day_file_bytes(path: Path, content: bytes) -> JobFile:
    path.write_bytes(content)
    return JobFile(path, len(content), hashlib.sha256(content).hexdigest())


def _failure_reason(url: str, unit_id: int) -> str:
    return str(
        scalar(
            url,
            "SELECT failure_reason FROM tick_archive_unit WHERE unit_id = %s",
            unit_id,
        )
    )


async def test_verify_fails_the_bad_header_unit_and_verifies_the_other(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    """Two *downloaded* units of one job, one file with a rewritten header."""
    url = migrated_tick_db
    job_dir = tmp_path / "GLBX-TEST-BADHEAD"
    job_dir.mkdir()
    files = {}
    for day in DAYS:
        content = day_file_bytes(FIXTURE, CME_DATASET, day, SType.PARENT)
        if day == DAYS[1]:
            content = bad_header_bytes(content, "stype_out")
        files[day] = write_day_file_bytes(
            job_dir / f"glbx-mdp3-{day:%Y%m%d}.trades.dbn.zst", content
        )
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(
            conn, range_start=DAYS[0], range_end=DAYS[-1] + timedelta(days=1)
        )
        ids = {
            day: insert_unit(
                conn,
                request,
                unit_date=day,
                state=UnitState.DOWNLOADED.value,
                file_path=str(files[day].path.relative_to(tmp_path)),
                file_size_bytes=files[day].size,
                file_sha256=files[day].sha256,
            )
            for day in DAYS
        }
    clock = FakeClock(START)
    async with connect(url) as conn:
        run = tick_run(conn, FakeTickProvider(clock=clock), tmp_path, clock)
        tally = await advance(run, DbnFileReader())
    assert (tally.verified, tally.failed) == (1, 1)
    assert unit_state(url, ids[DAYS[0]])[0] == UnitState.VERIFIED.value
    assert unit_state(url, ids[DAYS[1]])[0] == UnitState.DOWNLOADED.value
    reason = _failure_reason(url, ids[DAYS[1]])
    assert reason.startswith("header: "), reason
    assert "stype_out is raw_symbol" in reason


async def test_ingest_fails_the_bad_header_unit_and_ingests_the_other(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    good = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )
    bad = await seed_tier_unit(
        url, tmp_path, TickSchema.TBBO, TBBO_DAY, job_id="B", bad_header="mixed"
    )
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db)
    )
    assert report.outcome is TickOutcome.PARTIAL
    assert (report.summary["ingested"], report.summary["failed"]) == (1, 1)
    assert unit_state(url, good.unit_id)[0] == "ingested"
    assert unit_state(url, bad.unit_id)[0] == "verified"
    reason = _failure_reason(url, bad.unit_id)
    assert reason.startswith("decode: "), reason
    assert "mixed record types" in reason


async def test_delivery_fails_the_unclaimed_day_of_a_refused_header(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    """The job's second day is delivered with a refused header: that day
    fails naming the file (not a hole), the first downloads and verifies."""
    clock = FakeClock(START)
    provider = FakeTickProvider(clock=clock, bad_header_days={DAYS[1]: "ts_out"})
    request = request_for(TickSchema.TRADES, DAYS[0], DAYS[-1] + timedelta(days=1))
    async with connect(migrated_tick_db) as conn:
        run = tick_run(conn, provider, tmp_path, clock)
        pending = await insert_pending_request(
            conn, PlannedRequest("ES", request, DAYS), Decimal("0.002"), clock(), {}
        )
        job = provider.add_job(request)
        await record_submit(
            conn, pending.request_id, job.job_id, job.ts_received, clock()
        )
        tally = await advance(run, DbnFileReader())
        units = {
            u.unit_date: u for u in await units_of_request(conn, pending.request_id)
        }
    assert (tally.verified, tally.failed, tally.holed) == (1, 1, 0)
    assert units[DAYS[0]].state is UnitState.VERIFIED
    reason = _failure_reason(migrated_tick_db, units[DAYS[1]].unit_id)
    assert reason.startswith("header: no readable file for this day"), reason
    assert "ts_out records are not supported" in reason
