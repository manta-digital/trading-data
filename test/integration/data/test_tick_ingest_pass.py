"""The ingest pass over real slices (slice 225, TD2, TD4, TD8; FR1, FR2, FR7).

Failures mid-unit are injected by ``HookedReader``: its hook runs on the
worker thread after the unit's first batch has been copied.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg_pool import PoolTimeout
from tick_support.fake_provider import FakeClock, FakeTickProvider
from tick_support.ingest import (
    HookedReader,
    ingest_inputs,
    run_ingest_phase,
    scalar,
    trade_count,
    unit_state,
)
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit
from tick_support.runs import connect, tick_run
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, SeededUnit, seed_tier_unit
from tick_support.universe import use_untiered

from manta_trading.data.base.trading_calendar import TradingCalendar
from manta_trading.data.tick.acquisition_pass import AwaitTiming, run_pass
from manta_trading.data.tick.constants import SType, TickSchema, UnitState
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.manifest_repo import reset_exhausted
from manta_trading.data.tick.pass_contract import TickOutcome


@pytest.fixture(autouse=True)
def untiered(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests cover the pass with no tier chosen (``tick_support.universe``)."""
    use_untiered(monkeypatch)


TRADES_FILE = f"glbx-mdp3-{TRADES_DAY:%Y%m%d}.trades.dbn.zst"
TBBO_FILE = f"glbx-mdp3-{TBBO_DAY:%Y%m%d}.tbbo.dbn.zst"
UNTOUCHED = ("verified", "UNKNOWN", None, None)
WAIT_SECONDS = 10


async def _two_days(url: str, archive: Path) -> tuple[SeededUnit, SeededUnit]:
    trades = await seed_tier_unit(
        url, archive, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )
    tbbo = await seed_tier_unit(url, archive, TickSchema.TBBO, TBBO_DAY, job_id="B")
    return trades, tbbo


def _attempts(url: str, unit_id: int) -> int:
    return int(
        scalar(
            url,
            "SELECT attempt_count FROM tick_archive_unit WHERE unit_id = %s",
            unit_id,
        )
    )


def _terminate_others(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )


async def test_two_days_ingest_and_a_second_run_selects_nothing(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    trades, tbbo = await _two_days(url, tmp_path)
    inputs = ingest_inputs(url, session_migrated_db)
    report = await run_ingest_phase(url, tmp_path, inputs)
    assert report.outcome is TickOutcome.OK
    assert (report.summary["ingested"], report.summary["records"]) == (
        2,
        trades.record_count + tbbo.record_count,
    )
    rows = scalar(url, "SELECT count(*) FROM tick_trade")
    again = await run_ingest_phase(url, tmp_path, inputs)
    assert again.outcome is TickOutcome.OK
    assert (again.summary["ingested"], again.summary["units"]) == (0, [])
    assert scalar(url, "SELECT count(*) FROM tick_trade") == rows


async def test_a_dropped_database_aborts_keeping_committed_units(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    trades, tbbo = await _two_days(url, tmp_path)
    reader = HookedReader({TBBO_FILE: lambda: _terminate_others(url)})
    inputs = ingest_inputs(url, session_migrated_db, reader=reader, workers=1)
    report = await run_ingest_phase(url, tmp_path, inputs)
    assert report.outcome is TickOutcome.STORAGE_ABORT
    assert unit_state(url, trades.unit_id)[0] == "ingested"
    assert unit_state(url, tbbo.unit_id) == UNTOUCHED
    assert _attempts(url, tbbo.unit_id) == 0
    assert trade_count(url, tbbo.unit_id) == 0
    assert [u["unit_id"] for u in report.summary["units"]] == [trades.unit_id]


async def test_an_abort_settles_the_other_worker_before_the_run_ends(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    trades, tbbo = await _two_days(url, tmp_path)
    tbbo_started, trades_raising = threading.Event(), threading.Event()

    def trades_fails() -> None:
        assert tbbo_started.wait(WAIT_SECONDS)
        trades_raising.set()
        raise psycopg.OperationalError("simulated: the tick database went away")

    def tbbo_waits() -> None:
        tbbo_started.set()
        assert trades_raising.wait(WAIT_SECONDS)

    reader = HookedReader({TRADES_FILE: trades_fails, TBBO_FILE: tbbo_waits})
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db, reader=reader, workers=2)
    )
    assert report.outcome is TickOutcome.STORAGE_ABORT
    assert "simulated" in (report.error or "")
    assert unit_state(url, tbbo.unit_id)[0] == "ingested"
    assert [u["unit_id"] for u in report.summary["units"]] == [tbbo.unit_id]
    assert unit_state(url, trades.unit_id) == UNTOUCHED


async def test_a_failed_check_is_partial_exhausted_and_ingests_after_reset(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    seeded = await seed_tier_unit(
        url,
        tmp_path,
        TickSchema.TRADES,
        TRADES_DAY,
        job_id="T",
        provider_record_count=1,
    )
    inputs = ingest_inputs(url, session_migrated_db)
    report = await run_ingest_phase(url, tmp_path, inputs)
    assert (report.outcome, report.summary["failed"]) == (TickOutcome.PARTIAL, 1)
    state, status, _, _ = unit_state(url, seeded.unit_id)
    assert (state, status) == ("verified", "RETRY_EXHAUSTED")
    reason = scalar(
        url,
        "SELECT failure_reason FROM tick_archive_unit WHERE unit_id = %s",
        seeded.unit_id,
    )
    assert reason.startswith("counts: provider 1,")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_archive_unit SET provider_record_count = %s"
            " WHERE unit_id = %s",
            (seeded.record_count, seeded.unit_id),
        )
    async with connect(url) as conn:
        await reset_exhausted(conn, seeded.unit_id)
    after = await run_ingest_phase(url, tmp_path, inputs)
    assert (after.outcome, after.summary["ingested"]) == (TickOutcome.OK, 1)


async def test_acquisition_and_ingest_run_concurrently_without_interfering(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    await _two_days(url, tmp_path / "archive")
    (tmp_path / "acquisition").mkdir()
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    async with connect(url) as conn:
        run = tick_run(
            conn,
            FakeTickProvider(clock=clock),
            tmp_path / "acquisition",
            clock,
            timescale_db_url=session_migrated_db,
            tick_spend_ceiling_usd=Decimal("1"),
            tick_spend_30d_ceiling_usd=Decimal("5"),
        )
        acquisition, ingest = await asyncio.gather(
            run_pass(
                run,
                (None, None),
                False,
                DbnFileReader(),
                timing=AwaitTiming(budget_seconds=0, interval_seconds=1),
                free=lambda _: 10**12,
            ),
            run_ingest_phase(
                url, tmp_path / "archive", ingest_inputs(url, session_migrated_db)
            ),
        )
    assert ingest.outcome is TickOutcome.OK
    assert ingest.summary["ingested"] == 2
    assert acquisition.outcome is TickOutcome.OK, acquisition.to_dict()


async def test_a_row_lock_past_the_timeout_aborts_with_no_attempt(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    seeded = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )
    inputs = ingest_inputs(url, session_migrated_db, lock_timeout_seconds=1.0)
    with psycopg.connect(url) as holder:
        holder.execute(
            "SELECT 1 FROM tick_archive_unit WHERE unit_id = %s FOR UPDATE",
            (seeded.unit_id,),
        )
        report = await run_ingest_phase(url, tmp_path, inputs)
        holder.rollback()
    assert report.outcome is TickOutcome.STORAGE_ABORT
    assert "lock timeout" in (report.error or "")
    assert unit_state(url, seeded.unit_id) == UNTOUCHED
    assert _attempts(url, seeded.unit_id) == 0


async def test_a_unit_changed_mid_run_is_skipped_and_the_run_is_ok(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    seeded = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )

    def operator_resets() -> None:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(
                "UPDATE tick_archive_unit SET fetch_status = 'RETRY_EXHAUSTED',"
                " failure_reason = 'operator' WHERE unit_id = %s",
                (seeded.unit_id,),
            )

    reader = HookedReader({TRADES_FILE: operator_resets})
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db, reader=reader)
    )
    assert report.outcome is TickOutcome.OK
    assert report.summary["skipped"]["changed_during_ingest"] == 1
    assert report.summary["failed"] == 0
    assert _attempts(url, seeded.unit_id) == 0  # no failure recorded by ingest
    reason = scalar(
        url,
        "SELECT failure_reason FROM tick_archive_unit WHERE unit_id = %s",
        seeded.unit_id,
    )
    assert reason == "operator"
    assert trade_count(url, seeded.unit_id) == 0


async def test_an_unreachable_calendar_aborts_before_any_unit_starts(
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = migrated_tick_db
    seeded = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )

    def unreachable(*args: Any) -> list[Any]:
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    monkeypatch.setattr(TradingCalendar, "sessions_between", unreachable)
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db)
    )
    assert report.outcome is TickOutcome.STORAGE_ABORT
    assert "CME_EQUITY unavailable" in (report.error or "")
    assert unit_state(url, seeded.unit_id) == UNTOUCHED
    assert _attempts(url, seeded.unit_id) == 0


async def test_a_raw_symbol_unit_fails_as_shape_beside_a_good_one(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    url = migrated_tick_db
    good = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )
    shape = {"symbols": ["ESZ4"], "stype_in": SType.RAW_SYMBOL.value}
    with psycopg.connect(url, autocommit=True) as conn:
        units = {}
        for schema, state in (
            (TickSchema.DEFINITION, UnitState.INGESTED),
            (TickSchema.TRADES, UnitState.VERIFIED),
        ):
            request = insert_request(conn, schema=schema.value, **shape)
            units[schema] = insert_unit(
                conn, request, state=state.value, **FILE_COLUMNS
            )
    bad = units[TickSchema.TRADES]
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db)
    )
    assert (report.outcome, report.summary["ingested"]) == (TickOutcome.PARTIAL, 1)
    assert unit_state(url, good.unit_id)[0] == "ingested"
    assert unit_state(url, bad)[:2] == ("verified", "RETRY_EXHAUSTED")
    reason = scalar(
        url, "SELECT failure_reason FROM tick_archive_unit WHERE unit_id = %s", bad
    )
    assert reason.startswith("shape: stype_in raw_symbol")
