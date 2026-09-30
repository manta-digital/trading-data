"""The ingest worker on real slices (slice 225, TD2, TD8; FR3, FR5, FR8).

Every failure leaves no ``tick_trade`` rows, no ledger rows and no
transition: the unit's transaction is all or nothing.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from tick_support.ingest import (
    ledger_count,
    plan_unit,
    run_worker,
    scalar,
    trade_count,
    unit_state,
)
from tick_support.tier_units import TRADES_DAY, SeededUnit, seed_tier_unit

from manta_trading.data.base.session_index import SessionIndex
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_plan import Calendars, UnitIngestPlan
from manta_trading.data.tick.ingest_worker import UnitResult

VERIFIED_OPEN = ("verified", "UNKNOWN", None, None)


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


async def _seed(url: str, archive: Path, **kwargs: int) -> SeededUnit:
    return await seed_tier_unit(
        url, archive, TickSchema.TRADES, TRADES_DAY, job_id="JOB", **kwargs
    )


def _nothing_written(url: str, unit_id: int) -> None:
    assert unit_state(url, unit_id) == VERIFIED_OPEN
    assert trade_count(url, unit_id) == 0
    assert ledger_count(url, unit_id) == 0


async def test_a_real_day_ingests_with_a_complete_ledger(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    seeded = await _seed(migrated_tick_db, tmp_path)
    plan = await plan_unit(migrated_tick_db, calendars, seeded.unit_id)
    outcome = run_worker(plan, tmp_path, migrated_tick_db)
    assert (outcome.result, outcome.decoded, outcome.check) == (
        UnitResult.INGESTED,
        seeded.record_count,
        None,
    )
    assert unit_state(migrated_tick_db, seeded.unit_id) == (
        "ingested",
        "UNKNOWN",
        seeded.record_count,
        None,
    )
    assert trade_count(migrated_tick_db, seeded.unit_id) == seeded.record_count
    total = scalar(
        migrated_tick_db,
        "SELECT sum(record_count) FROM tick_ingest_ledger WHERE unit_id = %s",
        seeded.unit_id,
    )
    assert total == seeded.record_count
    zero = scalar(
        migrated_tick_db,
        "SELECT count(*) FROM tick_ingest_ledger WHERE unit_id = %s"
        " AND record_count = 0 AND first_event_ns IS NULL",
        seeded.unit_id,
    )
    assert zero > 0
    assert ledger_count(migrated_tick_db, seeded.unit_id) == 82  # 41 × 2 sessions


async def test_a_count_mismatch_fails_and_writes_nothing(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    seeded = await _seed(migrated_tick_db, tmp_path, provider_record_count=3775)
    plan = await plan_unit(migrated_tick_db, calendars, seeded.unit_id)
    outcome = run_worker(plan, tmp_path, migrated_tick_db)
    assert (outcome.result, outcome.check) == (UnitResult.FAILED, IngestCheck.COUNTS)
    assert outcome.reason == (
        f"counts: provider 3775, decoded {seeded.record_count},"
        f" stored {seeded.record_count}"
    )
    _nothing_written(migrated_tick_db, seeded.unit_id)


async def test_an_unresolved_record_fails_and_writes_nothing(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    seeded = await _seed(migrated_tick_db, tmp_path)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute("DELETE FROM tick_definition WHERE instrument_id = 46995")
    plan = await plan_unit(migrated_tick_db, calendars, seeded.unit_id)
    outcome = run_worker(plan, tmp_path, migrated_tick_db)
    assert (outcome.result, outcome.check) == (
        UnitResult.FAILED,
        IngestCheck.RESOLUTION,
    )
    assert "first instrument_id 46995" in (outcome.reason or "")
    _nothing_written(migrated_tick_db, seeded.unit_id)


def _shrunk(plan: UnitIngestPlan, *, first_close: bool) -> UnitIngestPlan:
    """The plan with the first session closing early (a "break"), or with the
    populated span ending at that early close (records "outside")."""
    first, second = plan.frame.index.sessions
    early = first.close_utc - timedelta(hours=10)
    if first_close:
        sessions = [dataclasses.replace(first, close_utc=early), second]
        frame = dataclasses.replace(plan.frame, index=SessionIndex(sessions))
    else:
        frame = dataclasses.replace(plan.frame, last_close=early)
    return dataclasses.replace(plan, frame=frame)


@pytest.mark.parametrize(
    ("first_close", "text"),
    [(True, "records in no session"), (False, "outside the populated calendar range")],
)
async def test_a_session_boundary_miss_fails_and_writes_nothing(
    migrated_tick_db: str,
    tmp_path: Path,
    calendars: Calendars,
    first_close: bool,
    text: str,
) -> None:
    seeded = await _seed(migrated_tick_db, tmp_path)
    plan = await plan_unit(migrated_tick_db, calendars, seeded.unit_id)
    outcome = run_worker(
        _shrunk(plan, first_close=first_close), tmp_path, migrated_tick_db
    )
    assert (outcome.result, outcome.check) == (
        UnitResult.FAILED,
        IngestCheck.SESSION_BOUNDARY,
    )
    assert text in (outcome.reason or "")
    _nothing_written(migrated_tick_db, seeded.unit_id)


@pytest.mark.parametrize("damage", ["missing", "truncated"])
async def test_an_unreadable_file_fails_as_decode_naming_the_path(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars, damage: str
) -> None:
    seeded = await _seed(migrated_tick_db, tmp_path)
    plan = await plan_unit(migrated_tick_db, calendars, seeded.unit_id)
    path = seeded.file.path
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes()[:20_000])
    outcome = run_worker(plan, tmp_path, migrated_tick_db)
    assert (outcome.result, outcome.check) == (UnitResult.FAILED, IngestCheck.DECODE)
    assert (outcome.reason or "").startswith(f"decode: {path}: ")
    _nothing_written(migrated_tick_db, seeded.unit_id)


async def test_a_unit_reset_mid_run_rolls_back_as_changed_during_ingest(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    seeded = await _seed(migrated_tick_db, tmp_path)
    plan = await plan_unit(migrated_tick_db, calendars, seeded.unit_id)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_archive_unit SET fetch_status = 'RETRY_EXHAUSTED',"
            " failure_reason = 'operator' WHERE unit_id = %s",
            (seeded.unit_id,),
        )
    outcome = run_worker(plan, tmp_path, migrated_tick_db)
    assert (outcome.result, outcome.check) == (UnitResult.CHANGED_DURING_INGEST, None)
    assert trade_count(migrated_tick_db, seeded.unit_id) == 0
    assert ledger_count(migrated_tick_db, seeded.unit_id) == 0
    assert unit_state(migrated_tick_db, seeded.unit_id)[:2] == (
        "verified",
        "RETRY_EXHAUSTED",
    )


async def test_a_held_row_lock_times_out_as_operational_error(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    seeded = await _seed(migrated_tick_db, tmp_path)
    plan = await plan_unit(migrated_tick_db, calendars, seeded.unit_id)
    with psycopg.connect(migrated_tick_db) as holder:
        holder.execute(
            "SELECT 1 FROM tick_archive_unit WHERE unit_id = %s FOR UPDATE",
            (seeded.unit_id,),
        )
        with pytest.raises(psycopg.OperationalError):
            run_worker(plan, tmp_path, migrated_tick_db, lock_timeout_seconds=1.0)
        holder.rollback()
    _nothing_written(migrated_tick_db, seeded.unit_id)


def _rows(url: str) -> list[tuple[object, ...]]:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT * FROM tick_trade ORDER BY instrument_id, ts_event, sequence,"
            " sequence_ordinal"
        ).fetchall()


async def test_re_ingesting_the_file_into_a_fresh_database_gives_identical_rows(
    migrated_tick_db: str,
    second_migrated_tick_db: str,
    tmp_path: Path,
    calendars: Calendars,
) -> None:
    for url, archive in (
        (migrated_tick_db, tmp_path / "a"),
        (second_migrated_tick_db, tmp_path / "b"),
    ):
        archive.mkdir()
        seeded = await _seed(url, archive)
        plan = await plan_unit(url, calendars, seeded.unit_id)
        assert run_worker(plan, archive, url).result is UnitResult.INGESTED
    first, second = _rows(migrated_tick_db), _rows(second_migrated_tick_db)
    assert len(first) == seeded.record_count
    assert first == second
