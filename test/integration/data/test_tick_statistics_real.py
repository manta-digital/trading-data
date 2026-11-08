"""Real ES statistics, 2024-12-03 and 2024-12-04, end to end (slice 303, D11).

The two files are whole purchased days (job ``GLBX-20261105-7C6BWV8WXS``): session
2024-12-03's statistics window. They are ingested through the worker with the
committed real 2024-12-03 definitions, beside the committed real ``tbbo`` and
derived ``ohlcv-1m`` days for the volume check. Days the window reaches but no
file covers (2024-12-02) are empty ingested units.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest
from tick_support.ingest import plan_unit, run_worker, scalar
from tick_support.rows import insert_day_condition
from tick_support.runs import connect
from tick_support.statistics_seed import seed_unit
from tick_support.tier_units import TBBO_DAY, real_file, seed_tier_unit
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.data.base.session_index import Session
from manta_trading.data.tick import universe
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.family_report import FamilyStatus, family_reports
from manta_trading.data.tick.ingest_plan import Calendars
from manta_trading.data.tick.ingest_worker import UnitResult
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_constants import STATISTICS_TABLE, StatType
from manta_trading.data.tick.statistics_reads import (
    VolumeClass,
    resolved,
    volume_check,
)
from manta_trading.data.tick.status_reads import calendar_sessions, shape_of
from manta_trading.data.tick.universe import HistoryWant

runner = CliRunner(env={"COLUMNS": "200"})

SESSION = TBBO_DAY
NEXT_DAY = date(2024, 12, 4)
WINDOW = [date(2024, 12, 2), SESSION, NEXT_DAY]
ES = next(e for e in universe.TICK_UNIVERSE if e.product == "ES")
WITH_STATISTICS = replace(
    ES,
    tier=TickSchema.TBBO,
    start=SESSION,
    end=date(2024, 12, 5),
    history=(
        HistoryWant(TickSchema.DEFINITION, SESSION, date(2024, 12, 5)),
        HistoryWant(TickSchema.STATISTICS, SESSION, date(2024, 12, 5)),
    ),
)
ARGS = [
    "data", "tick", "coverage", "--product", "ES", "--start", f"{SESSION}",
    "--end", f"{NEXT_DAY}", "--schema", "statistics", "--json",
]  # fmt: skip
#: Records in each day's file, measured on the purchased files (task 8).
RECORDS = {SESSION: 1099, NEXT_DAY: 1503}


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


async def _ingest(url: str, calendars: Calendars, archive: Path, unit: int) -> None:
    plan = await plan_unit(url, calendars, unit)
    outcome = run_worker(plan, archive, url)
    assert outcome.result is UnitResult.INGESTED, outcome.reason


@pytest.fixture
async def real_window(
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
    calendars: Calendars,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[str]:
    url, archive = migrated_tick_db, tmp_path / "archive"
    definitions = real_file(SESSION, TickSchema.DEFINITION)
    units = [
        await seed_tier_unit(
            url, archive, TickSchema.STATISTICS, SESSION, job_id="STATS1203"
        ),
        await seed_tier_unit(
            url,
            archive,
            TickSchema.STATISTICS,
            NEXT_DAY,
            job_id="STATS1204",
            definition_file=definitions,
        ),
        await seed_tier_unit(url, archive, TickSchema.TBBO, SESSION, job_id="TBBO"),
        await seed_tier_unit(url, archive, TickSchema.OHLCV_1M, SESSION, job_id="BARS"),
    ]
    for seeded in units:
        await _ingest(url, calendars, archive, seeded.unit_id)
    with psycopg.connect(url, autocommit=True) as conn:
        seed_unit(conn, TickSchema.STATISTICS, WINDOW[0])
        seed_unit(conn, TickSchema.TBBO, WINDOW[0])
        for day in WINDOW:
            insert_day_condition(conn, condition_date=day, condition="available")
    monkeypatch.setattr(universe, "TICK_UNIVERSE", (WITH_STATISTICS,))
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=url,
        timescale_db_url=session_migrated_db,
    )
    with (
        patch("manta_trading.cli.app.Settings", side_effect=lambda: settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield url


def _session(calendar_url: str) -> Session:
    (found,) = [
        s
        for s in calendar_sessions(
            calendar_url,
            "CME_EQUITY",
            datetime.combine(SESSION, time(), UTC),
            datetime.combine(SESSION + timedelta(days=1), time(), UTC),
        )
        if s.session_date == SESSION
    ]
    return found


async def test_every_record_is_stored_and_the_referenced_ones_get_their_session(
    real_window: str,
) -> None:
    with psycopg.connect(real_window) as conn:
        for day, records in RECORDS.items():
            row = conn.execute(
                "SELECT u.provider_record_count, u.decoded_record_count,"
                f" (SELECT count(*) FROM {STATISTICS_TABLE} s"
                " WHERE s.unit_id = u.unit_id)"
                " FROM tick_archive_unit u JOIN tick_request r USING (request_id)"
                " WHERE r.schema = 'statistics' AND u.unit_date = %s"
                " AND u.provider_record_count IS NOT NULL",
                (day,),
            ).fetchone()
            assert row is not None
            assert row == (records, records, records)
    with psycopg.connect(real_window) as conn:
        sessions = conn.execute(
            "SELECT DISTINCT session_date FROM tick_statistics"
            " WHERE stat_type = ANY(%s) ORDER BY 1",
            ([int(t) for t in StatType],),
        ).fetchall()
        sessionless = conn.execute(
            f"SELECT count(*) FROM {STATISTICS_TABLE}"
            " WHERE session_date IS NULL AND stat_type = ANY(%s)",
            ([int(t) for t in StatType],),
        ).fetchone()
    assert sessionless == (0,)
    # Final volume and interest for the session before, preliminary settlements
    # for the session itself, in the file of the day they arrive.
    assert [row[0] for row in sessions][:2] == [date(2024, 12, 2), SESSION]


async def test_revisions_resolve_to_one_value_per_contract_statistic_and_session(
    real_window: str,
) -> None:
    async with connect(real_window) as conn:
        values = await resolved(
            conn,
            shape_of(ES),
            list(StatType),
            date(2024, 12, 2),
            NEXT_DAY,
        )
    keys = [(v.instrument_id, v.stat_type, v.session_date) for v in values]
    assert len(keys) == len(set(keys)) > 0
    stored = scalar(
        real_window,
        "SELECT count(*) FROM (SELECT DISTINCT instrument_id, stat_type, session_date"
        f" FROM {STATISTICS_TABLE} WHERE session_date IS NOT NULL"
        " AND stat_type = ANY(%s)) k",
        [int(t) for t in StatType],
    )
    assert len(keys) == stored


@pytest.mark.parametrize("family", [SchemaFamily.TICK_TIER, SchemaFamily.BAR_1M])
async def test_traded_never_exceeds_cleared_and_never_goes_missing(
    real_window: str, session_migrated_db: str, family: SchemaFamily
) -> None:
    async with connect(real_window) as conn:
        found = await volume_check(
            conn,
            shape_of(ES),
            family,
            "ES",
            [_session(session_migrated_db)],
        )
    assert found
    findings = {v.verdict for v in found} & {VolumeClass.OVER, VolumeClass.NO_TRADES}
    assert not findings, [v for v in found if v.verdict in findings]


def test_coverage_agrees_on_the_real_files_and_a_tampered_copy_exits_3(
    real_window: str,
) -> None:
    clean = runner.invoke(app, ARGS)
    assert clean.exit_code == 0, clean.output
    (es,) = json.loads(clean.stdout)["products"]
    (session,) = es["sessions"]
    assert session["ledger"] == session["raw"] > 0 and session["mismatches"] == []
    with psycopg.connect(real_window, autocommit=True) as conn:
        deleted = conn.execute(
            f"DELETE FROM {STATISTICS_TABLE} WHERE stat_type = %s"
            f" AND session_date = %s AND ctid = (SELECT ctid FROM {STATISTICS_TABLE}"
            " WHERE stat_type = %s AND session_date = %s LIMIT 1)",
            (int(StatType.SETTLEMENT_PRICE), SESSION) * 2,
        )
        assert deleted.rowcount == 1
    result = runner.invoke(app, ARGS)
    assert result.exit_code == 3, result.output


def test_the_report_cross_tier_counts_equal_what_coverage_reports(
    real_window: str, session_migrated_db: str
) -> None:
    """Criterion 6: the verdicts the report carries are ``coverage``'s, for the
    same range, from the real purchased files."""

    async def report() -> tuple[FamilyStatus, ...]:
        async with connect(real_window) as conn:
            return await family_reports(
                conn, session_migrated_db, WITH_STATISTICS, None, cross_tier=True
            )

    # ``coverage`` runs its own event loop, so this test is sync and runs
    # the report in one of its own.
    reports = asyncio.run(report())
    (tier,) = [r for r in reports if r.family == SchemaFamily.TICK_TIER.value]
    (result,) = tier.cross_tier
    covered = runner.invoke(app, ARGS)
    assert covered.exit_code == 0, covered.output
    (es,) = json.loads(covered.stdout)["products"]
    expected = Counter(
        v["verdict"]
        for v in es["volume_checks"]
        if v["family"] == SchemaFamily.TICK_TIER.value
    )
    assert sum(expected.values()) > 0
    assert {c.value: getattr(result, c.value) for c in VolumeClass} == {
        c.value: expected.get(c.value, 0) for c in VolumeClass
    }
    statistics = [r for r in reports if r.family == SchemaFamily.STATISTICS.value]
    assert statistics and not statistics[0].cross_tier  # only volume families
