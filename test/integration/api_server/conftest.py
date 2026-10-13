"""Fixtures for the futures API against throwaway databases (slice 231).

Every client is built with ``create_app(db_url=…, tick_db_url=…)``: the primary
is the session-scoped migrated throwaway (it holds the CME calendar), and the
tick URL is a function-scoped migrated throwaway, a closed port, or unset. The
guard keeps the developer's ``MT_TICK_DB_URL`` out of the unset case.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.api import no_dev_tick_settings  # noqa: F401  (autouse guard)
from tick_support.cli import closed_port
from tick_support.roll_seed import seed_september_roll
from tick_support.rows import (
    FILE_COLUMNS,
    insert_definition,
    insert_request,
    insert_unit,
)

from manta_trading.api_server.app import create_app
from manta_trading.api_server.routes import futures_catalog, futures_params
from manta_trading.data.tick import constants
from manta_trading.data.tick.constants import (
    CME_EQUITY_CALENDAR_ID,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

#: A password only the closed-port URL carries, for the no-secret checks (FR11).
CLOSED_PORT_SECRET = "t1ck-s3cret"


def client_for(primary_url: str, tick_url: str | None) -> Iterator[TestClient]:
    """A client over the real ``lifespan``, closed (pools and all) on exit."""
    with TestClient(create_app(db_url=primary_url, tick_db_url=tick_url)) as client:
        yield client


def closed_port_url() -> str:
    return f"postgresql://tick:{CLOSED_PORT_SECRET}@127.0.0.1:{closed_port()}/tick"


@pytest.fixture
def tick_client(
    session_migrated_db: str, migrated_tick_db: str
) -> Iterator[TestClient]:
    yield from client_for(session_migrated_db, migrated_tick_db)


@pytest.fixture
def closed_tick_client(session_migrated_db: str) -> Iterator[TestClient]:
    yield from client_for(session_migrated_db, closed_port_url())


@pytest.fixture
def unconfigured_tick_client(session_migrated_db: str) -> Iterator[TestClient]:
    yield from client_for(session_migrated_db, None)


# -- two-product universe (Integration Requirements; 232 needs no change) -----

#: The second product of the two-product universe, and its one contract.
SECOND = TickUniverseEntry("GC", ("GC.FUT",), SType.PARENT, None, None, None)
SECOND_CONTRACT_ID = 777


def definition_unit(url: str) -> int:
    """An ingested definition unit, for definitions a test adds."""
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.DEFINITION.value)
        return insert_unit(
            conn,
            request,
            unit_date=date(2024, 8, 30),
            state=UnitState.INGESTED.value,
            **FILE_COLUMNS,
        )


def seed_second_product(url: str) -> None:
    unit = definition_unit(url)
    with psycopg.connect(url, autocommit=True) as conn:
        insert_definition(
            conn,
            unit,
            instrument_id=SECOND_CONTRACT_ID,
            raw_symbol="GCZ4",
            asset=SECOND.product,
        )


@pytest.fixture
def two_products(migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """ES plus a second product on a calendar the test primary holds; the
    routes read the universe through ``futures_params`` and the catalog."""
    universe = (*TICK_UNIVERSE, SECOND)
    for module in (futures_catalog, futures_params):
        monkeypatch.setattr(module, "TICK_UNIVERSE", universe)
    monkeypatch.setitem(
        constants.FUTURES_PRODUCT_CALENDAR,  # type: ignore[arg-type]
        SECOND.product,
        CME_EQUITY_CALENDAR_ID,
    )
    seed_september_roll(migrated_tick_db)
    seed_second_product(migrated_tick_db)
