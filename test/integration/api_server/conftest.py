"""Fixtures for the futures API against throwaway databases (slice 231).

Every client is built with ``create_app(db_url=…, tick_db_url=…)``: the primary
is the session-scoped migrated throwaway (it holds the CME calendar), and the
tick URL is a function-scoped migrated throwaway, a closed port, or unset. The
guard keeps the developer's ``MT_TICK_DB_URL`` out of the unset case.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tick_support.api import (  # noqa: F401  (no_dev_tick_settings: autouse guard)
    SECOND,
    client_for,
    closed_port_url,
    no_dev_tick_settings,
    seed_second_product,
)
from tick_support.roll_seed import seed_september_roll

from manta_trading.api_server.routes import futures_catalog, futures_params
from manta_trading.data.tick import constants
from manta_trading.data.tick.constants import (
    CME_EQUITY_CALENDAR_ID,
)
from manta_trading.data.tick.universe import TICK_UNIVERSE


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


# -- two-product universe (Integration Requirements) ---------------------------


@pytest.fixture
def two_products(migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """ES plus a synthetic second product on a calendar the test primary holds;
    the routes read the universe through ``futures_params`` and the catalog.
    Pinned to ES, so later universe edits (232's GC) never reach these tests."""
    es = next(entry for entry in TICK_UNIVERSE if entry.product == "ES")
    universe = (es, SECOND)
    for module in (futures_catalog, futures_params):
        monkeypatch.setattr(module, "TICK_UNIVERSE", universe)
    monkeypatch.setitem(
        constants.FUTURES_PRODUCT_CALENDAR,  # type: ignore[arg-type]
        SECOND.product,
        CME_EQUITY_CALENDAR_ID,
    )
    seed_september_roll(migrated_tick_db)
    seed_second_product(migrated_tick_db)
