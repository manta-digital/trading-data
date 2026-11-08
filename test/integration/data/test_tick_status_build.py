"""``build_status`` and ``build_coverage`` after real ingests (slice 225, FR9, FR10).

Two real days are ingested (2024-09-03 trades, 2024-12-03 tbbo). With no tier
set, the scope is the sessions those days touch. A unit with no rows is added
for 2024-09-02 so session 09-03 has every day ingested; that unit also brings
session 09-02 into scope. Conditions give the sessions different buckets.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
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
from tick_support.tick_bar_helpers import seed_bar_units
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit
from tick_support.universe import UNTIERED

from manta_trading.cli.commands.tick_status_render import _product_lines
from manta_trading.data.tick.constants import DatasetCondition, TickSchema, UnitState
from manta_trading.data.tick.family_report import LOAD_COMPLETE_ONLY
from manta_trading.data.tick.roll_types import Unresolved
from manta_trading.data.tick.tick_coverage import build_coverage
from manta_trading.data.tick.tick_status_build import build_status
from manta_trading.data.tick.universe import HistoryWant

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "tick_status"
NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
OBSERVED = datetime(2026, 9, 30, 11, tzinfo=UTC)
ES = UNTIERED[0]
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
    ingested: AConn, migrated_tick_db: str, session_migrated_db: str
) -> None:
    status = await build_status(
        ingested, session_migrated_db, UNTIERED, NOW, tick_url=migrated_tick_db
    )
    (es,) = [p for p in status.products if p.product == "ES"]  # only ES is seeded
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
        ingested,
        session_migrated_db,
        UNTIERED,
        NOW,
        tick_url=migrated_tick_db,
        all_instruments=True,
    )
    (every_es,) = [p for p in every.products if p.product == "ES"]
    assert len(every_es.contracts) == len(es.contracts) + es.spreads_hidden


async def test_coverage_is_ok_then_names_a_deleted_row(
    ingested: AConn, session_migrated_db: str
) -> None:
    start, end = date(2024, 9, 3), date(2024, 9, 5)
    coverage = await build_coverage(ingested, session_migrated_db, ES, start, end, None)
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
    after = await build_coverage(ingested, session_migrated_db, ES, start, end, None)
    assert after.mismatched
    (bad,) = [s for s in after.sessions if s.mismatches]
    (mismatch,) = bad.mismatches
    assert mismatch.instrument_id == 46995
    assert mismatch.ledger == mismatch.raw + 1
    body = after.to_dict()
    assert body["mismatched"] is True
    assert json.loads(json.dumps(body)) == body


async def test_caught_up_is_judged_over_the_wanted_range_only(
    ingested: AConn, migrated_tick_db: str, session_migrated_db: str
) -> None:
    """226 TD9: the window [09-03, 09-04) meets the sessions dated 09-03 and
    09-04 (which opens 09-03 22:00 UTC); with 09-04 held too, both are
    complete. Held sessions outside it (09-05 lacks its 09-05 day, 12-03 its
    12-02 day) are listed but do not decide caught up."""
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        request = insert_request(
            conn, range_start=date(2024, 9, 4), range_end=date(2024, 9, 5)
        )
        insert_unit(
            conn,
            request,
            unit_date=date(2024, 9, 4),
            state=UnitState.INGESTED.value,
            **FILE_COLUMNS,
        )
    entry = replace(
        UNTIERED[0],
        tier=TickSchema.TRADES,
        start=date(2024, 9, 3),
        end=date(2024, 9, 4),
    )
    status = await build_status(
        ingested, session_migrated_db, [entry], NOW, tick_url=migrated_tick_db
    )
    [product] = status.products
    assert product.buckets["complete"] >= 1
    assert sum(n for b, n in product.buckets.items() if b != "complete") > 0
    assert product.caught_up is True


# -- 302: families ------------------------------------------------------------

TIERED = replace(ES, tier=TickSchema.TBBO, start=date(2024, 9, 2), end=date(2024, 9, 5))
WITH_BARS = replace(
    TIERED,
    history=(
        HistoryWant(TickSchema.DEFINITION, date(2024, 12, 3), date(2024, 12, 5)),
        HistoryWant(TickSchema.OHLCV_1M, date(2024, 12, 3), date(2024, 12, 5)),
    ),
)


def _without_families(body: dict[str, Any]) -> dict[str, Any]:
    """The pre-302 JSON shape: ``families`` and ``cross_tier_not_active`` are
    the added keys."""
    body = {k: v for k, v in body.items() if k != "cross_tier_not_active"}
    return {
        **body,
        "products": [
            {k: v for k, v in product.items() if k != "families"}
            for product in body["products"]
        ],
    }


@pytest.mark.parametrize(
    ("name", "universe"), [("es_status", UNTIERED), ("es_tiered_status", [TIERED])]
)
async def test_output_for_an_entry_with_no_bar_want_is_unchanged(
    ingested: AConn,
    migrated_tick_db: str,
    session_migrated_db: str,
    name: str,
    universe: Any,
) -> None:
    """Golden files captured from the commit before the family work."""
    status = await build_status(
        ingested, session_migrated_db, universe, NOW, tick_url=migrated_tick_db
    )
    (product,) = [p for p in status.products if p.product == "ES"]
    expected_text = (GOLDEN / f"{name}.txt").read_text()
    assert "\n".join(_product_lines(product)) + "\n" == expected_text
    assert LOAD_COMPLETE_ONLY not in expected_text
    expected_json = json.loads((GOLDEN / f"{name}.json").read_text())
    assert _without_families(status.to_dict()) == expected_json


async def test_an_entry_with_a_bar_want_reports_a_separate_load_complete_section(
    ingested: AConn,
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
) -> None:
    url = migrated_tick_db
    await seed_bar_units(url, tmp_path, job_id="BARS")
    report = await run_ingest_phase(
        url, tmp_path, ingest_inputs(url, session_migrated_db)
    )
    assert report.summary["ingested"] == 1
    with psycopg.connect(url, autocommit=True) as conn:
        for day in (date(2024, 12, 2), date(2024, 12, 4)):  # bare, sparse units
            request = insert_request(
                conn,
                schema="ohlcv-1m",
                range_start=day,
                range_end=day + timedelta(days=1),
            )
            insert_unit(
                conn,
                request,
                unit_date=day,
                state=UnitState.INGESTED.value,
                **FILE_COLUMNS,
            )
        insert_day_condition(
            conn, condition_date=date(2024, 12, 4), condition="available"
        )
        insert_day_condition(
            conn, condition_date=date(2024, 12, 5), condition="missing"
        )
    plain = await build_status(
        ingested, session_migrated_db, [TIERED], NOW, tick_url=url
    )
    status = await build_status(
        ingested, session_migrated_db, [WITH_BARS], NOW, tick_url=url
    )
    (es,) = status.products
    (plain_es,) = plain.products
    tier, bars = es.families
    assert (tier.family, bars.family) == ("tick_tier", "bar_1m")
    assert bars.schemas == ("ohlcv-1m",)
    # Sparse bars are still complete; a missing day with no unit is a hole. The
    # session of 12-02 is in scope only because the bare 12-02 unit touches it
    # (its other day has no row), and it is outside the wanted range.
    assert {k: v for k, v in bars.buckets.items() if v} == {
        "complete": 2,
        "provider_hole": 1,
        "edge_unknown": 1,
    }
    assert bars.holed_sessions == (date(2024, 12, 5),)
    assert bars.caught_up is True  # the hole is on a provider-missing day
    # The tick tier's own fields are untouched by the bar want; its roll lines
    # differ only in naming the bar family's status where no basis is complete
    # (303 D10).
    assert replace(es, families=(), rolls=()) == replace(
        plain_es, families=(), rolls=()
    )
    for line, plain_line in zip(es.rolls, plain_es.rolls, strict=True):
        assert replace(line, answer=None) == replace(plain_line, answer=None)
        if isinstance(line.answer, Unresolved):
            assert isinstance(plain_line.answer, Unresolved)
            assert line.answer.reason.startswith(plain_line.answer.reason)
            assert "bar_1m" in line.answer.reason
        else:
            assert line.answer == plain_line.answer
    assert tier.buckets == es.buckets
    text = "\n".join(_product_lines(es))
    assert text.count(LOAD_COMPLETE_ONLY) == 1
    assert "ohlcv-1m   load-complete only" in text
    body = status.to_dict()
    assert [f["family"] for f in body["products"][0]["families"]] == [
        "tick_tier",
        "bar_1m",
    ]
    assert json.loads(json.dumps(body)) == body


async def test_a_definition_only_history_prints_no_bar_section(
    ingested: AConn, migrated_tick_db: str, session_migrated_db: str
) -> None:
    entry = replace(
        TIERED,
        history=(HistoryWant(TickSchema.DEFINITION, date(2024, 12, 3)),),
    )
    status = await build_status(
        ingested, session_migrated_db, [entry], NOW, tick_url=migrated_tick_db
    )
    (es,) = status.products
    assert [f.family for f in es.families] == ["tick_tier"]
    assert LOAD_COMPLETE_ONLY not in "\n".join(_product_lines(es))
