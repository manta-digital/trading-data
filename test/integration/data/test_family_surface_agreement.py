"""The four surfaces judge the same sessions (slice 305, criterion 3).

``status``, accounting, health and the API block all take their figures from
``family_reports``. One database holds two products with every family, a missing
session, a provider-hole day and a statistics window still open at the dataset
edge; each surface is built from it and must agree on every product and family.
Health carries no bucket map, so its row's detail is parsed back.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
from tick_support.rows import insert_dataset_edge, insert_day_condition
from tick_support.runs import connect
from tick_support.statistics_seed import seed_unit
from tick_support.universe import UNTIERED

from manta_trading.api_server.models.futures import TickBlock
from manta_trading.cli.commands.health_tick import family_check
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.family_report import (
    FamilyStatus,
    ReadProgress,
    futures_completeness,
)
from manta_trading.data.tick.tick_accounting import accounting_lines
from manta_trading.data.tick.tick_status_build import build_status
from manta_trading.data.tick.universe import HistoryWant

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
FIRST, END = date(2024, 12, 2), date(2024, 12, 7)  # Mon..Fri, end exclusive
#: Thursday midday: Wednesday's statistics window (through Thursday's close) is
#: open, so Wednesday and Thursday are not yet judged.
EDGE = datetime(2024, 12, 5, 12, tzinfo=UTC)
WANTS = tuple(
    replace(
        entry,
        tier=TickSchema.TBBO,
        start=FIRST,
        end=END,
        history=(
            HistoryWant(TickSchema.OHLCV_1M, FIRST, END),
            HistoryWant(TickSchema.STATISTICS, FIRST, END),
        ),
    )
    for entry in UNTIERED[:2]
)
DAYS = [date(2024, 12, 1) + timedelta(days=n) for n in range(6)]  # Sun..Fri
#: Per product: the day with no unit but a condition of ``available`` (a missing
#: session) and the day the provider lacks (a hole).
GAP_DAY = {WANTS[0].product: date(2024, 12, 3), WANTS[1].product: date(2024, 12, 2)}
HOLE_DAY = {WANTS[0].product: date(2024, 12, 5), WANTS[1].product: date(2024, 12, 3)}
FAMILIES = (TickSchema.TBBO, TickSchema.OHLCV_1M, TickSchema.STATISTICS)


def _seed(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        insert_dataset_edge(conn, available_end=EDGE)
        for entry in WANTS:
            symbols = list(entry.symbols)
            for day in DAYS:
                if day in (GAP_DAY[entry.product], HOLE_DAY[entry.product], DAYS[-1]):
                    continue  # no unit: a gap, a hole, or past the dataset edge
                for schema in FAMILIES:
                    seed_unit(conn, schema, day, symbols=symbols)
        for day in DAYS:
            condition = "available"
            if day in HOLE_DAY.values():
                condition = "missing"
            elif day == DAYS[-1]:
                condition = "pending"  # published after the dataset edge
            insert_day_condition(conn, condition_date=day, condition=condition)


_ROW = re.compile(
    r"(?P<complete>[\d,]+)/(?P<total>[\d,]+) complete to (?P<edge>\d{4}-\d\d-\d\d);"
    r" (?P<verdict>caught up|not caught up)(?:: (?P<counts>[^;]+))?"
)


def _health(row: str) -> dict[str, Any]:
    found = _ROW.match(row)
    assert found, row
    counts: dict[str, int] = {}
    for part in (found["counts"] or "").split(", "):
        if part:
            number, _, name = part.partition(" ")
            counts[name.replace(" ", "_")] = int(number)
    return {
        "complete": int(found["complete"].replace(",", "")),
        "total": int(found["total"].replace(",", "")),
        "edge": date.fromisoformat(found["edge"]),
        "caught_up": found["verdict"] == "caught up",
        "counts": counts,
    }


async def test_status_accounting_health_and_the_api_judge_the_same_sessions(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    _seed(migrated_tick_db)
    async with connect(migrated_tick_db) as conn:
        status = await build_status(
            conn, session_migrated_db, WANTS, NOW, tick_url=migrated_tick_db
        )
        reports = await futures_completeness(
            conn, session_migrated_db, WANTS, ReadProgress(), cross_tier=True
        )
        no_cross = await futures_completeness(
            conn, session_migrated_db, WANTS, ReadProgress(), cross_tier=False
        )
    lines = accounting_lines(reports)
    api = TickBlock.from_result(accounting_lines(no_cross))
    seen: set[str] = set()

    for product in status.products:
        (api_product,) = [p for p in api.products if p.product == product.product]
        assert [f.family for f in api_product.families] == [
            f.family for f in product.families
        ]
        for family in product.families:
            (line,) = [
                x
                for x in lines
                if (x.product, x.family) == (product.product, family.family)
            ]
            (served,) = [f for f in api_product.families if f.family == family.family]
            for surface in (line, served):
                assert surface.buckets == family.buckets
                assert surface.caught_up == family.caught_up
                assert surface.source_edge == family.source_edge
            report = _report(reports, product.product, family.family)
            parsed = _health(family_check(product.product, report).detail)
            assert parsed["complete"] == family.buckets["complete"]
            assert parsed["total"] == sum(family.buckets.values())
            assert parsed["caught_up"] == family.caught_up
            assert family.source_edge is not None
            assert parsed["edge"] == family.source_edge.date()
            assert parsed["counts"] == {
                k: v for k, v in family.buckets.items() if v and k != "complete"
            }
            seen |= {k for k, v in family.buckets.items() if v}

    # The seed really exercises each case, or agreement would be vacuous.
    assert {"missing", "provider_hole", "pending"} <= seen


def _report(
    reports: dict[str, tuple[FamilyStatus, ...]], product: str, family: str
) -> FamilyStatus:
    (found,) = [r for r in reports[product] if r.family == family]
    return found
