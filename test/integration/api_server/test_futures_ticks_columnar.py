"""Both ``/ticks`` routes serve the columnar body (slice 308 D6, FR7).

Seeded by ``tick_bar_seed``: ESU4 in 2024-09-17, ESZ4 in 2024-09-18 (repeated
``(ts_event, sequence)`` keys, quotes on half the trades) and 2024-09-19.
Decoding a body through JSON and through msgpack must reproduce ``read_ticks``'
rows exactly, ``sequence_ordinal`` included and ``bid_ct``/``ask_ct`` excepted.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import msgpack
import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.columnar import decode_body, wire_row
from tick_support.roll_seed import SeededRoll
from tick_support.tick_bar_seed import FIRST_COUNT, seed_tick_bar_sessions

from manta_trading.api_server.futures_read import PlanFor, read_series
from manta_trading.data.tick.get_plan import GetPlan, contract_plan, product_plan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_reads import Row, read_ticks
from manta_trading.data.tick.universe import TICK_UNIVERSE

ROLL = {"start": "2024-09-17", "end": "2024-09-20"}
SPAN = (date(2024, 9, 17), date(2024, 9, 20))
TICK = SchemaFamily.TICK_TIER


@pytest.fixture
def seeded(migrated_tick_db: str) -> SeededRoll:
    return seed_tick_bar_sessions(migrated_tick_db)


@pytest.fixture
def client(seeded: SeededRoll, tick_client: TestClient) -> TestClient:
    return tick_client


async def _rows(tick_url: str, plan_for: PlanFor) -> list[Row]:
    async with await psycopg.AsyncConnection.connect(tick_url, autocommit=True) as conn:
        return (await read_series(conn, plan_for, read_ticks, 10**6))[1]


def _contract(calendar_url: str) -> PlanFor:
    async def plan_for(conn: Conn) -> GetPlan:
        return await contract_plan(conn, calendar_url, "ESZ4", *SPAN, TICK)

    return plan_for


def _product(tick_url: str, calendar_url: str) -> PlanFor:
    entry = next(e for e in TICK_UNIVERSE if e.product == "ES")

    async def plan_for(conn: Conn) -> GetPlan:
        urls = (tick_url, calendar_url)
        return await product_plan(conn, urls, entry, DEFAULT_ROLL_RULE, *SPAN, TICK)

    return plan_for


def _bodies(client: TestClient, path: str) -> list[dict[str, Any]]:
    as_json = client.get(path, params=ROLL)
    packed = client.get(path, params={**ROLL, "format": "msgpack"})
    assert as_json.status_code == packed.status_code == 200
    return [as_json.json(), msgpack.unpackb(packed.content)]


async def test_contract_ticks_decode_to_the_read_rows(
    client: TestClient, migrated_tick_db: str, session_migrated_db: str
) -> None:
    expected = await _rows(migrated_tick_db, _contract(session_migrated_db))

    for body in _bodies(client, "/api/v1/futures/contracts/ESZ4/ticks"):
        assert decode_body(body) == [wire_row(r) for r in expected]
        assert body["count"] == len(expected)
        assert [b["session_date"] for b in body["blocks"]] == [
            "2024-09-18",
            "2024-09-19",
        ]
    assert any(row[-1] == 1 for row in expected), "repeated keys are seeded"


async def test_product_ticks_decode_to_the_read_rows_one_block_per_session(
    client: TestClient, migrated_tick_db: str, session_migrated_db: str
) -> None:
    expected = await _rows(
        migrated_tick_db, _product(migrated_tick_db, session_migrated_db)
    )

    for body in _bodies(client, "/api/v1/futures/products/ES/ticks"):
        assert decode_body(body) == [wire_row(r) for r in expected]
        assert [(b["session_date"], b["raw_symbol"]) for b in body["blocks"]] == [
            ("2024-09-17", "ESU4"),
            ("2024-09-18", "ESZ4"),
            ("2024-09-19", "ESZ4"),
        ]
        assert body["blocks"][1]["count"] == FIRST_COUNT


def test_a_quoteless_tick_keeps_every_column(client: TestClient) -> None:
    body = client.get("/api/v1/futures/contracts/ESZ4/ticks", params=ROLL).json()

    block = body["blocks"][0]
    for name in ("bid_px", "ask_px", "bid_sz", "ask_sz"):
        assert len(block[name]) == block["count"]
        assert None in block[name] and any(v is not None for v in block[name])
