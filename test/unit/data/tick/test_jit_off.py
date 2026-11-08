"""Every tick read connection turns JIT off (slice 305 D10).

A statement over ``tick_statistics`` plans across every chunk, so its estimated
cost clears ``jit_above_cost`` and PostgreSQL JIT-compiles thousands of
functions for a scan that takes a fraction of a second (37.4 s with JIT, 0.42 s
without, on the manifest-scale seed). The three ways a surface opens a tick
connection each set it; these tests pin that without a database.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from manta_trading.api_server.tick_access import _configure
from manta_trading.cli import tick_composed
from manta_trading.config import Settings
from manta_trading.constants import DB_BULK_SESSION
from manta_trading.data.tick.constants import TICK_JIT_OPTION, TICK_JIT_STATEMENT
from manta_trading.data.tick.store_context import TickDatabaseUnreachable, _connect

URL = "postgresql://nobody:secret@127.0.0.1:1/none"


class _Refused:
    """Stands in for ``AsyncConnection.connect``: records the call, refuses it."""

    def __init__(self) -> None:
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.args, self.kwargs = args, kwargs
        raise psycopg.OperationalError("refused")


@pytest.fixture
def refused(monkeypatch: pytest.MonkeyPatch) -> _Refused:
    stub = _Refused()
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", stub)
    return stub


async def test_the_store_connection_sets_jit_off(refused: _Refused) -> None:
    with pytest.raises(TickDatabaseUnreachable):
        await _connect(URL)
    assert refused.kwargs["options"] == TICK_JIT_OPTION


async def test_the_composed_connection_keeps_its_options_and_sets_jit_off(
    refused: _Refused,
) -> None:
    settings = Settings(_env_file=None, tick_db_url=URL)  # type: ignore[call-arg]
    await tick_composed.connect(settings)
    conninfo = refused.args[0]
    assert (
        "statement_timeout" in conninfo and "default_transaction_read_only" in conninfo
    )
    assert TICK_JIT_OPTION.removeprefix("-c ") in conninfo


async def test_the_api_pool_configures_jit_off() -> None:
    statements: list[str] = []

    class _Conn:
        async def set_autocommit(self, value: bool) -> None: ...

        async def execute(self, statement: str) -> None:
            statements.append(statement)

    await _configure(DB_BULK_SESSION)(_Conn())  # type: ignore[arg-type]
    assert TICK_JIT_STATEMENT in statements
