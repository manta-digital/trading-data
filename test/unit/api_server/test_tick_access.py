"""``tick_errors``: the one error mapping every futures route uses (231 TD2).

The app is built without its lifespan; ``app.state`` carries the two values
the mapping reads (the tick URL, for scrubbing, and the statement timeout).
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server import tick_access
from manta_trading.api_server.app import create_app
from manta_trading.api_server.routes.futures_params import TickBarGranularity
from manta_trading.api_server.tick_access import tick_errors
from manta_trading.data.tick.active_contract import RollRangeTooLong
from manta_trading.data.tick.constants import TICK_BAR_GRANULARITIES
from manta_trading.data.tick.get_plan import ContractAmbiguous, ContractNotFound
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.data.tick.tick_calendar import TickCalendarError

SECRET = "pw-in-url"
ROUTE = "/test/raise"


def _app_raising(exc: BaseException) -> FastAPI:
    app = create_app()
    app.state.tick_url = f"postgresql://tick:{SECRET}@127.0.0.1:1/tick"
    app.state.statement_timeout = "20s"

    @app.get(ROUTE)
    async def raising() -> None:
        with tick_errors(app):
            raise exc

    return app


def _get(exc: BaseException) -> tuple[int, str]:
    client = TestClient(_app_raising(exc), raise_server_exceptions=False)
    response = client.get(ROUTE)
    return response.status_code, response.json()["error"]


def test_a_cancelled_statement_keeps_the_504() -> None:
    status, error = _get(psycopg.errors.QueryCanceled("canceling statement"))

    assert status == 504
    assert "narrow the requested range" in error


@pytest.mark.parametrize(
    "exc",
    [
        psycopg.OperationalError(f"connection to tick:{SECRET} refused"),
        TickCalendarError("calendar unreachable"),
    ],
)
def test_an_outage_is_503_unreachable_without_the_password(
    exc: BaseException,
) -> None:
    status, error = _get(exc)

    assert status == 503
    assert error.startswith("unreachable: ")
    assert SECRET not in error


def test_an_unknown_contract_is_404() -> None:
    assert _get(ContractNotFound("no definition for ESZ9")) == (
        404,
        "no definition for ESZ9",
    )


@pytest.mark.parametrize(
    "exc",
    [
        ContractAmbiguous("ESZ4 names two instruments"),
        RollRangeTooLong("spans 2000 sessions"),
        TickPreflightError("2030-01-01 → 2030-02-01 is outside the calendar"),
    ],
)
def test_a_refusal_is_422_with_its_message(exc: BaseException) -> None:
    assert _get(exc) == (422, str(exc))


def test_anything_else_stays_a_sanitized_500() -> None:
    assert _get(RuntimeError("boom")) == (500, "internal server error")


# -- review fixes (231 code review) --------------------------------------------


class _Pool:
    """Records checkouts and returns, standing in for the tick pool."""

    def __init__(self) -> None:
        self.returned: list[object] = []
        self.conn = object()

    async def getconn(self) -> object:
        return self.conn

    async def putconn(self, conn: object) -> None:
        self.returned.append(conn)


@pytest.mark.parametrize(
    "error", [asyncio.CancelledError(), RuntimeError("unforeseen")]
)
async def test_a_failed_migration_check_returns_the_connection(
    error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F003: a pool of four must not leak a checkout on any exit."""
    pool = _Pool()
    app = create_app()
    app.state.tick_pool = pool

    async def failing(_conn: object) -> list[str]:
        raise error

    monkeypatch.setattr(tick_access, "missing_migrations", failing)

    with pytest.raises(type(error)):
        await tick_access._checkout(app)

    assert pool.returned == [pool.conn]


def test_the_bar_size_enum_is_the_tick_bar_tuple() -> None:
    """F001: the enum is built from ``TICK_BAR_GRANULARITIES``; this pins it."""
    assert [m.value for m in TickBarGranularity] == [
        g.value for g in TICK_BAR_GRANULARITIES
    ]
