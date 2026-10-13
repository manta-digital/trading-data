"""Keep a developer's tick database out of API tests (slice 231).

``lifespan`` builds ``Settings()``, which reads ``.env``. With
``create_app(tick_db_url=None)`` every API test would then open a pool against
the dev tick database named by ``MT_TICK_DB_URL``. The guard patches the
``Settings`` the app module uses so both tick URLs read as unset; a test that
wants a tick database passes ``tick_db_url=`` explicitly, as the load tier does
for the primary. Production code is unchanged.

Enabled by importing :func:`no_dev_tick_settings` into a conftest (or a test
module): it is autouse, so it applies to everything under that file. The
client and seed helpers below serve the API integration tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from unittest.mock import patch

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tick_support.cli import closed_port
from tick_support.rows import (
    FILE_COLUMNS,
    insert_definition,
    insert_request,
    insert_unit,
)

from manta_trading.api_server.app import create_app
from manta_trading.config import Settings
from manta_trading.data.tick.constants import SType, TickSchema, UnitState
from manta_trading.data.tick.universe import TickUniverseEntry

_APP_SETTINGS = "manta_trading.api_server.app.Settings"


def settings_without_tick() -> Settings:
    """The loaded settings with both tick URLs unset."""
    return Settings().model_copy(
        update={"tick_db_url": None, "tick_maintenance_url": None}
    )


@pytest.fixture(autouse=True)
def no_dev_tick_settings() -> Iterator[None]:
    with patch(_APP_SETTINGS, side_effect=settings_without_tick):
        yield


def tick_not_configured(app: FastAPI) -> FastAPI:
    """The ``app.state`` a lifespan leaves with no tick URL, for unit tests
    that build an app without entering its lifespan."""
    app.state.tick_pool = None
    app.state.tick_url = None
    app.state.tick_unset = "MT_TICK_DB_URL unset"
    app.state.calendar_url = "postgresql://unused@127.0.0.1:1/unused"
    return app


#: A password only the closed-port URL carries, for the no-secret checks (FR11).
CLOSED_PORT_SECRET = "t1ck-s3cret"


def client_for(primary_url: str, tick_url: str | None) -> Iterator[TestClient]:
    """A client over the real ``lifespan``, closed (pools and all) on exit."""
    with TestClient(create_app(db_url=primary_url, tick_db_url=tick_url)) as client:
        yield client


def closed_port_url() -> str:
    return f"postgresql://tick:{CLOSED_PORT_SECRET}@127.0.0.1:{closed_port()}/tick"


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
