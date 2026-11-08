"""The statistics family's verdict and content findings (slice 303, D7).

Units, definitions and ledger rows are seeded directly: the verdict reads only
the manifest, ``tick_statistics_ledger``, ``tick_definition`` and the calendar.
Session 2024-12-03 opens Monday 23:00 UTC; its window is 12-02 to 12-04 (the
close day of the session after it).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import psycopg
from tick_support.rows import (
    insert_dataset_edge,
    insert_day_condition,
    insert_statistics_ledger_row,
)
from tick_support.runs import connect
from tick_support.statistics_seed import seed_outright, seed_unit, seed_units
from tick_support.universe import UNTIERED

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.constants import SPREAD_CLASS, TickSchema
from manta_trading.data.tick.family_report import StatisticsFamilyStatus
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_constants import StatType
from manta_trading.data.tick.statistics_status import (
    ContentFinding,
    content_findings,
    statistics_verdicts,
)
from manta_trading.data.tick.status_reads import calendar_sessions
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_build import build_status
from manta_trading.data.tick.universe import HistoryWant

SESSION = date(2024, 12, 3)
WINDOW = [date(2024, 12, 2), date(2024, 12, 3), date(2024, 12, 4)]
INSTRUMENT, OTHER, EXPIRING, SPREAD = 42_035_063, 42_035_064, 42_035_065, 42_004_904
ALL_TYPES = [int(t) for t in StatType]
NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
ES = UNTIERED[0]
WANTS_STATISTICS = replace(
    ES,
    tier=TickSchema.TBBO,
    start=date(2024, 12, 3),
    end=date(2024, 12, 4),
    history=(
        HistoryWant(TickSchema.DEFINITION, date(2024, 12, 3), date(2024, 12, 5)),
        HistoryWant(TickSchema.STATISTICS, date(2024, 12, 3), date(2024, 12, 5)),
    ),
)
WITHOUT_STATISTICS = replace(WANTS_STATISTICS, history=())


def _ledger(
    conn: psycopg.Connection[Any], unit: int, instrument: int, *types: int
) -> None:
    for stat_type in types:
        insert_statistics_ledger_row(
            conn,
            unit,
            instrument_id=instrument,
            session_date=SESSION,
            stat_type=stat_type,
        )


def _session(url: str) -> Session:
    (found,) = [
        s
        for s in calendar_sessions(
            url,
            "CME_EQUITY",
            datetime.combine(SESSION, time(), UTC),
            datetime.combine(SESSION + timedelta(days=1), time(), UTC),
        )
        if s.session_date == SESSION
    ]
    return found


async def _verdict(
    tick_url: str, calendar_url: str, entry: Any = WANTS_STATISTICS
) -> Any:
    session = _session(calendar_url)
    async with connect(tick_url) as conn:
        read = statistics_verdicts(calendar_url)
        (verdict,) = await read(conn, entry, [session], SchemaFamily.STATISTICS)
        return session, verdict


async def test_a_session_is_complete_only_when_its_whole_window_is_loaded(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        seed_units(
            conn, TickSchema.STATISTICS, WINDOW[:2]
        )  # the day after the session is not loaded
        insert_day_condition(conn, condition_date=WINDOW[2], condition="available")
        for day in WINDOW[:2]:
            insert_day_condition(conn, condition_date=day, condition="available")
    _, partial = await _verdict(migrated_tick_db, session_migrated_db)
    assert partial.status is TickSessionStatus.MISSING
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        seed_unit(conn, TickSchema.STATISTICS, WINDOW[2])
    _, whole = await _verdict(migrated_tick_db, session_migrated_db)
    assert whole.status is TickSessionStatus.COMPLETE
    assert [facts.day for facts in whole.days] == WINDOW


async def test_a_day_the_provider_lacks_is_a_provider_hole(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        seed_units(conn, TickSchema.STATISTICS, WINDOW[:2])
        for day in WINDOW[:2]:
            insert_day_condition(conn, condition_date=day, condition="available")
        insert_day_condition(conn, condition_date=WINDOW[2], condition="missing")
    _, verdict = await _verdict(migrated_tick_db, session_migrated_db)
    assert verdict.status is TickSessionStatus.PROVIDER_HOLE
    assert verdict.provider_missing


async def _complete_window(url: str) -> list[int]:
    with psycopg.connect(url, autocommit=True) as conn:
        units = seed_units(conn, TickSchema.STATISTICS, WINDOW)
        for day in WINDOW:
            insert_day_condition(conn, condition_date=day, condition="available")
        definition = seed_unit(conn, TickSchema.DEFINITION, SESSION)
        seed_outright(conn, definition, INSTRUMENT)
        seed_outright(conn, definition, OTHER)
        seed_outright(
            conn,
            definition,
            EXPIRING,
            expiration_ns=int(datetime(2024, 12, 3, 12, tzinfo=UTC).timestamp())
            * 1_000_000_000,
        )
        seed_outright(conn, definition, SPREAD, instrument_class=SPREAD_CLASS)
    return units


async def test_a_reporting_outright_is_owed_every_type_it_reports_and_a_settlement(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    units = await _complete_window(migrated_tick_db)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        _ledger(conn, units[1], INSTRUMENT, *ALL_TYPES)
        _ledger(conn, units[1], OTHER, int(StatType.CLEARED_VOLUME))
        _ledger(conn, units[2], OTHER, int(StatType.OPEN_INTEREST))
    session, verdict = await _verdict(migrated_tick_db, session_migrated_db)
    async with connect(migrated_tick_db) as conn:
        findings = await content_findings(conn, WANTS_STATISTICS, [session], [verdict])
    # The expiring contract and the spread have no records and are not required.
    assert findings == (
        ContentFinding(SESSION, OTHER, f"ES{OTHER}", (int(StatType.SETTLEMENT_PRICE),)),
    )


async def test_a_contract_that_only_ever_settles_needs_only_a_settlement(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    units = await _complete_window(migrated_tick_db)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        _ledger(conn, units[1], INSTRUMENT, *ALL_TYPES)
        # A deferred month: CME publishes its settlement and nothing else.
        _ledger(conn, units[1], OTHER, int(StatType.SETTLEMENT_PRICE))
    session, verdict = await _verdict(migrated_tick_db, session_migrated_db)
    async with connect(migrated_tick_db) as conn:
        assert (
            await content_findings(conn, WANTS_STATISTICS, [session], [verdict]) == ()
        )


async def test_a_session_that_is_not_load_complete_has_no_findings(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        seed_units(conn, TickSchema.STATISTICS, WINDOW[:1])
        definition = seed_unit(conn, TickSchema.DEFINITION, SESSION)
        seed_outright(conn, definition, INSTRUMENT)
    session, verdict = await _verdict(migrated_tick_db, session_migrated_db)
    assert verdict.status is not TickSessionStatus.COMPLETE
    async with connect(migrated_tick_db) as conn:
        assert (
            await content_findings(conn, WANTS_STATISTICS, [session], [verdict]) == ()
        )


async def test_the_status_section_appears_only_with_a_statistics_want(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    units = await _complete_window(migrated_tick_db)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        _ledger(conn, units[1], INSTRUMENT, *ALL_TYPES)
        insert_dataset_edge(conn, available_end=datetime(2030, 1, 1, tzinfo=UTC))
    async with connect(migrated_tick_db) as conn:
        with_want = await build_status(
            conn,
            session_migrated_db,
            [WANTS_STATISTICS],
            NOW,
            tick_url=migrated_tick_db,
        )
        without = await build_status(
            conn,
            session_migrated_db,
            [WITHOUT_STATISTICS],
            NOW,
            tick_url=migrated_tick_db,
        )
    (product,) = with_want.products
    (section,) = [f for f in product.families if f.family == SchemaFamily.STATISTICS]
    assert isinstance(section, StatisticsFamilyStatus)
    assert section.buckets["complete"] >= 1
    assert {finding.instrument_id for finding in section.findings} == {OTHER}
    body = with_want.to_dict()
    assert json.loads(json.dumps(body)) == body
    (statistics_json,) = [
        f for f in body["products"][0]["families"] if f["family"] == "statistics"
    ]
    # A contract with no records is owed only the settlement CME gives every one.
    assert statistics_json["findings"][0]["missing_stat_types"] == [
        int(StatType.SETTLEMENT_PRICE)
    ]
    (bare,) = without.products
    assert [f.family for f in bare.families] == [SchemaFamily.TICK_TIER]
    assert "findings" not in without.to_dict()["products"][0]["families"][0]
