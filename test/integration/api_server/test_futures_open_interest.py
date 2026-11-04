"""``rule=n.0`` and the segment ``basis`` on the futures routes (slice 303, D10, D13).

The September 2024 roll is seeded as in ``test_tick_active_open_interest``: ESU4
then ESZ4 on 09-18 under ``v.0`` and ``n.0`` alike, with statistics for every
session.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.roll_seed import (
    ESH5,
    ESU4,
    ESZ4,
    SEPTEMBER,
    SEPTEMBER_CROSS,
    seed_september_roll,
    seed_tier_days,
)
from tick_support.rows import insert_statistics

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.statistics_constants import StatType

PRODUCT = "/api/v1/futures/products/{}/{}"
RANGE = {"start": "2024-09-16", "end": "2024-09-20"}
CALENDAR_DAYS = [date(2024, 9, 1) + timedelta(days=n) for n in range(32)]


def _interest(day: date, contract: Any) -> int:
    crossed = day >= SEPTEMBER_CROSS
    if contract is ESU4:
        return 4_000 if crossed else 6_000
    return (7_000 if crossed else 3_000) if contract is ESZ4 else 50


@pytest.fixture
def client(migrated_tick_db: str, tick_client: TestClient) -> TestClient:
    seed_september_roll(migrated_tick_db)
    units = seed_tier_days(migrated_tick_db, CALENDAR_DAYS, TickSchema.STATISTICS)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        for sequence, day in enumerate(SEPTEMBER):
            for contract in (ESU4, ESZ4, ESH5):
                insert_statistics(
                    conn,
                    units[day],
                    instrument_id=contract.instrument_id,
                    stat_type=int(StatType.OPEN_INTEREST),
                    quantity=_interest(day, contract),
                    price=None,
                    session_date=day,
                    sequence=sequence,
                )
    return tick_client


def test_the_schedule_answers_n0_and_names_its_basis(client: TestClient) -> None:
    body = client.get(
        PRODUCT.format("ES", "schedule"), params={**RANGE, "rule": "n.0"}
    ).json()
    assert [s["raw_symbol"] for s in body["segments"]] == ["ESU4", "ESZ4"]
    assert {s["basis"] for s in body["segments"]} == {"statistics"}
    assert {a["basis_family"] for a in body["answers"]} == {"statistics"}


def test_a_product_read_under_each_rule_names_the_tier_it_ranked_on(
    client: TestClient,
) -> None:
    for rule, basis in (("v.0", "tick_tier"), ("n.0", "statistics"), ("c.0", None)):
        body = client.get(
            PRODUCT.format("ES", "bars"),
            params={**RANGE, "granularity": "1d", "rule": rule},
        ).json()
        assert body["segments"], rule
        assert {s["basis"] for s in body["segments"]} == {basis}, rule


def test_an_offset_on_n_is_422_with_the_parsers_message(client: TestClient) -> None:
    response = client.get(
        PRODUCT.format("ES", "schedule"), params={**RANGE, "rule": "n.0+2s"}
    )
    assert response.status_code == 422
    assert "offset suffix is allowed only on calendar" in response.text


def test_a_contract_read_has_no_basis(client: TestClient) -> None:
    body = client.get(
        "/api/v1/futures/contracts/ESZ4/bars",
        params={"start": "2024-09-18", "end": "2024-09-19", "granularity": "1d"},
    ).json()
    assert [s["basis"] for s in body["segments"]] == [None]
