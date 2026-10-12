"""The tick line's states from ``tick_composed`` (slice 230, TD8, FR14).

No database: a closed local port stands in for an unreachable cluster, and
the connection and migration check are faked for the other states.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import psycopg
import pytest

from manta_trading.cli import tick_composed
from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS
from manta_trading.data.tick.line_state import (
    TickLineState,
    TickUnavailable,
    unavailable_text,
)

SECRET = "Sw0rdf1sh-tick-pw"


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _settings(url: str | None) -> Settings:
    return Settings(_env_file=None, tick_db_url=url)  # type: ignore[call-arg]


def _dead_url() -> str:
    return f"postgresql://reader:{SECRET}@127.0.0.1:{_closed_port()}/trading_tick"


class _FakeConn:
    closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def no_tick_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MT_TICK_DB_URL", raising=False)


def test_unset_url_is_not_configured(no_tick_env: None) -> None:
    line = asyncio.run(tick_composed.open(_settings(None)))

    assert line == TickUnavailable(TickLineState.NOT_CONFIGURED, "MT_TICK_DB_URL unset")
    assert unavailable_text(line) == "not configured (MT_TICK_DB_URL unset)"


def test_refused_port_is_unreachable_and_never_shows_the_password(
    no_tick_env: None,
) -> None:
    line = asyncio.run(tick_composed.open(_settings(_dead_url())))

    assert isinstance(line, TickUnavailable)
    assert line.state is TickLineState.UNREACHABLE
    text = unavailable_text(line)
    assert text.startswith("unreachable: ")
    assert "\n" not in text
    assert SECRET not in text and SECRET not in line.detail


def test_a_password_in_an_error_is_scrubbed(no_tick_env: None) -> None:
    error = psycopg.OperationalError(f"auth failed for {SECRET}\nsecond line")

    line = tick_composed.classify(error, _settings(_dead_url()))

    assert line.detail == "auth failed for ***"


def test_pending_migrations(no_tick_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeConn()

    async def connect(*_: Any, **__: Any) -> _FakeConn:
        return fake

    async def missing(_: Any) -> list[str]:
        return ["tick_009_example"]

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(tick_composed, "missing_migrations", missing)

    line = asyncio.run(tick_composed.open(_settings(_dead_url())))

    assert isinstance(line, TickUnavailable)
    assert unavailable_text(line) == (
        "migrations pending (mt data migrate apply --track tick)"
    )
    assert fake.closed


def test_a_late_statement_timeout_is_unreachable_naming_it(
    no_tick_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeConn()

    async def opened(_: Settings) -> _FakeConn:
        return fake

    async def read(_: Any) -> int:
        raise psycopg.errors.QueryCanceled(
            "canceling statement due to statement timeout"
        )

    monkeypatch.setattr(tick_composed, "open", opened)

    line = tick_composed.read_line(_settings(_dead_url()), read)

    assert isinstance(line, TickUnavailable)
    assert unavailable_text(line) == (
        f"unreachable: statement timeout ({TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS} s):"
        " canceling statement due to statement timeout"
    )
    assert fake.closed


def test_a_good_read_returns_its_value(
    no_tick_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeConn()

    async def opened(_: Settings) -> _FakeConn:
        return fake

    async def read(_: Any) -> int:
        return 42

    monkeypatch.setattr(tick_composed, "open", opened)

    assert tick_composed.read_line(_settings(_dead_url()), read) == 42
    assert fake.closed
