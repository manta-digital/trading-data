"""The futures catalog routes and ``catalog_reads`` (slice 231, FR7; TD1, TD2).

The September 2024 roll is seeded (``roll_seed``): ESU4 → ESZ4 under ``v.0``
on 09-18. The calendar is the session primary's CME_EQUITY. CLI comparisons
run ``mt data tick active --json`` in-process over the same databases.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.cli import clear_tick_env, cli_settings, closed_port
from tick_support.roll_seed import ESH5, ESU4, ESZ4, SPREAD_ID, seed_september_roll
from tick_support.rows import (
    FILE_COLUMNS,
    insert_definition,
    insert_request,
    insert_unit,
)
from typer.testing import CliRunner

from manta_trading.api_server.routes import futures_catalog, futures_params
from manta_trading.cli.app import app as cli_app
from manta_trading.data.tick import constants
from manta_trading.data.tick.active_contract import RollInputError
from manta_trading.data.tick.catalog_reads import contracts_for
from manta_trading.data.tick.constants import (
    CME_EQUITY_CALENDAR_ID,
    TICK_ROLL_MAX_SESSIONS,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

from .conftest import client_for

CONTRACTS = "/api/v1/futures/products/{}/contracts"
PRODUCTS = "/api/v1/futures/products"
SCHEDULE = "/api/v1/futures/products/{}/schedule"
SEPTEMBER = {"start": "2024-09-05", "end": "2024-09-28"}
#: A defined contract with no ledger rows: its ``held`` is null.
UNHELD = ESH5
#: The second product of the two-product universe, and its one contract.
SECOND = TickUniverseEntry("GC", ("GC.FUT",), SType.PARENT, None, None, None)
SECOND_CONTRACT_ID = 777


@pytest.fixture
def seeded(migrated_tick_db: str, tick_client: TestClient) -> TestClient:
    seed_september_roll(migrated_tick_db)
    return tick_client


def _cli_json(
    monkeypatch: pytest.MonkeyPatch, tick_url: str, calendar_url: str, *args: str
) -> Any:
    clear_tick_env(monkeypatch)
    with cli_settings(tick_url, calendar_url):
        result = CliRunner().invoke(cli_app, ["data", "tick", "active", *args])
    assert result.exit_code in (0, 3), result.output
    return json.loads(result.stdout)


# -- catalog_reads (5.2) -------------------------------------------------------


async def test_contracts_for_lists_held_ranges_in_expiration_order(
    migrated_tick_db: str,
) -> None:
    seed_september_roll(migrated_tick_db)
    async with await psycopg.AsyncConnection.connect(
        migrated_tick_db, autocommit=True
    ) as conn:
        rows = await contracts_for(conn, "ES")
        other = await contracts_for(conn, "GC")

    by_id = {r.instrument_id: r for r in rows}
    outrights = [r.raw_symbol for r in rows if r.instrument_id != SPREAD_ID]
    assert outrights == ["ESU4", "ESZ4", "ESH5"]
    assert by_id[ESU4.instrument_id].held == (date(2024, 9, 3), date(2024, 9, 27))
    assert by_id[ESZ4.instrument_id].held == (date(2024, 9, 3), date(2024, 9, 27))
    assert by_id[UNHELD.instrument_id].held is not None  # 5 a day in the seed
    assert other == []


async def test_a_defined_contract_with_no_ledger_rows_has_no_held_range(
    migrated_tick_db: str,
) -> None:
    _seed_second_product(migrated_tick_db)
    async with await psycopg.AsyncConnection.connect(
        migrated_tick_db, autocommit=True
    ) as conn:
        (row,) = await contracts_for(conn, SECOND.product)

    assert (row.instrument_id, row.held) == (SECOND_CONTRACT_ID, None)


# -- contracts (5.4) -----------------------------------------------------------


def test_contracts_lists_the_seeded_definitions(seeded: TestClient) -> None:
    response = seeded.get(CONTRACTS.format("ES"))

    assert response.status_code == 200
    body = response.json()
    assert body["product"] == "ES"
    symbols = [c["raw_symbol"] for c in body["contracts"]]
    outrights = [
        c["raw_symbol"] for c in body["contracts"] if c["instrument_class"] == "F"
    ]
    assert outrights == ["ESU4", "ESZ4", "ESH5"] and "ESU4-ESZ4" in symbols
    esu4 = body["contracts"][0]
    assert esu4["held"] == {"first_session": "2024-09-03", "last_session": "2024-09-27"}
    assert esu4["expiration"] == "2024-09-20T13:30:00+00:00"


def test_contracts_formats_fixed_point_fields_as_decimal_text(
    migrated_tick_db: str, tick_client: TestClient
) -> None:
    unit = _definition_unit(migrated_tick_db)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        insert_definition(
            conn,
            unit,
            instrument_id=4242,
            raw_symbol="ESM5",
            min_price_increment=250_000_000,
            unit_of_measure_qty=50_000_000_000,
        )

    (record,) = tick_client.get(CONTRACTS.format("ES")).json()["contracts"]

    assert (record["min_price_increment"], record["unit_of_measure_qty"]) == (
        "0.25",
        "50",
    )


def test_an_unknown_product_is_404(seeded: TestClient) -> None:
    response = seeded.get(CONTRACTS.format("ZZ"))

    assert (response.status_code, response.json()) == (
        404,
        {"error": "Product 'ZZ' not found"},
    )


@pytest.mark.parametrize(
    "path", [PRODUCTS, CONTRACTS.format("ES"), SCHEDULE.format("ES")]
)
def test_every_catalog_route_is_503_with_the_tick_database_down(
    closed_tick_client: TestClient, path: str
) -> None:
    assert closed_tick_client.get(path).status_code == 503


# -- products (5.6) ------------------------------------------------------------


def test_products_matches_the_cli_active_lines(
    seeded: TestClient,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = seeded.get(PRODUCTS).json()
    cli = _cli_json(
        monkeypatch, migrated_tick_db, session_migrated_db, "--product", "ES", "--json"
    )

    (es,) = body["products"]
    assert (es["product"], es["calendar"]) == ("ES", CME_EQUITY_CALENDAR_ID)
    assert [line["rule"] for line in es["active"]] == ["v.0", "c.0"]
    assert es["active"] == cli["lines"]


def test_a_product_with_nothing_held_is_a_reason(tick_client: TestClient) -> None:
    response = tick_client.get(PRODUCTS)

    assert response.status_code == 200
    reasons = {line["reason"] for line in response.json()["products"][0]["active"]}
    assert reasons == {"no sessions held for ES"}


def test_a_refusal_is_the_line_error_with_http_200(
    seeded: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*_: Any) -> None:
        raise RollInputError("ESU4 has two definitions for 2024-09-18")

    monkeypatch.setattr(futures_catalog, "latest_lines", refuse)

    response = seeded.get(PRODUCTS)

    assert response.status_code == 200
    errors = {line["error"] for line in response.json()["products"][0]["active"]}
    assert errors == {"ESU4 has two definitions for 2024-09-18"}


def test_a_calendar_outage_is_503(
    session_migrated_db: str, migrated_tick_db: str
) -> None:
    """A live tick database with the calendar (primary) unreachable in the roll
    path: an outage, not a line error."""
    seed_september_roll(migrated_tick_db)
    for client in client_for(session_migrated_db, migrated_tick_db):
        client.app.state.calendar_url = (  # type: ignore[attr-defined]
            f"postgresql://x@127.0.0.1:{closed_port()}/x"
        )
        response = client.get(PRODUCTS)

    assert response.status_code == 503
    assert response.json()["error"].startswith("unreachable: ")


# -- schedule (5.8) ------------------------------------------------------------


def test_schedule_matches_the_cli_schedule(
    seeded: TestClient,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = seeded.get(SCHEDULE.format("ES"), params={**SEPTEMBER, "rule": "v.0"}).json()
    cli = _cli_json(
        monkeypatch,
        migrated_tick_db,
        session_migrated_db,
        *("--product", "ES", "--rule", "v.0", "--json"),
        *("--start", SEPTEMBER["start"], "--end", SEPTEMBER["end"]),
    )

    assert body == cli["schedules"][0]
    assert [s["raw_symbol"] for s in body["segments"]] == ["ESU4", "ESZ4"]
    assert {"definition_unit_id", "basis_unit_ids"} <= set(body["answers"][0])


def test_schedule_defaults_to_the_held_sessions(seeded: TestClient) -> None:
    body = seeded.get(SCHEDULE.format("ES")).json()

    assert (body["rule"], body["is_default"]) == ("v.0", True)
    assert body["answers"][-1]["session_date"] == "2024-09-27"


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"rule": "x.0"}, "unknown roll method 'x'"),
        (
            {"start": "2020-01-02", "end": "2026-01-02"},
            f"TICK_ROLL_MAX_SESSIONS ({TICK_ROLL_MAX_SESSIONS})",
        ),
        ({"start": "2024-09-10", "end": "2024-09-10"}, "is empty (end is exclusive)"),
    ],
    ids=["bad-rule", "over-cap", "empty"],
)
def test_schedule_refusals_are_422(
    seeded: TestClient, params: dict[str, str], message: str
) -> None:
    response = seeded.get(SCHEDULE.format("ES"), params=params)

    assert response.status_code == 422
    assert message in response.json()["error"]


# -- two-product universe (Integration Requirements; 232 needs no change) -----


def _definition_unit(url: str) -> int:
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.DEFINITION.value)
        return insert_unit(
            conn,
            request,
            unit_date=date(2024, 8, 30),
            state=UnitState.INGESTED.value,
            **FILE_COLUMNS,
        )


def _seed_second_product(url: str) -> None:
    unit = _definition_unit(url)
    with psycopg.connect(url, autocommit=True) as conn:
        insert_definition(
            conn,
            unit,
            instrument_id=SECOND_CONTRACT_ID,
            raw_symbol="GCZ4",
            asset=SECOND.product,
        )


@pytest.fixture
def two_products(
    migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """ES plus a second product on a calendar the test primary holds."""
    universe = (*TICK_UNIVERSE, SECOND)
    for module in (futures_catalog, futures_params):
        monkeypatch.setattr(module, "TICK_UNIVERSE", universe)
    monkeypatch.setitem(
        constants.FUTURES_PRODUCT_CALENDAR,  # type: ignore[arg-type]
        SECOND.product,
        CME_EQUITY_CALENDAR_ID,
    )
    seed_september_roll(migrated_tick_db)
    _seed_second_product(migrated_tick_db)
    yield


def test_a_second_product_is_served_with_no_route_change(
    two_products: None, tick_client: TestClient
) -> None:
    products = tick_client.get(PRODUCTS).json()["products"]
    contracts = tick_client.get(CONTRACTS.format(SECOND.product))
    schedule = tick_client.get(SCHEDULE.format(SECOND.product))

    assert [p["product"] for p in products] == ["ES", SECOND.product]
    assert contracts.status_code == 200
    assert contracts.json()["contracts"][0]["raw_symbol"] == "GCZ4"
    assert schedule.status_code == 200
    assert schedule.json()["segments"] == []
