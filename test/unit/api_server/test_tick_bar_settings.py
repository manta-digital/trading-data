"""``MT_API_MAX_TICK_BAR_TRADES``: the tick-bar trade-scan ceiling (308 D5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from manta_trading.api_server.deps import get_max_tick_bar_trades
from manta_trading.config import Settings
from manta_trading.constants import API_MAX_TICK_BAR_TRADES

ENV = "MT_API_MAX_TICK_BAR_TRADES"


def test_default_is_the_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert Settings(_env_file=None).api_max_tick_bar_trades == API_MAX_TICK_BAR_TRADES
    assert API_MAX_TICK_BAR_TRADES == 2_000_000


@pytest.mark.parametrize("value", ["0", "-5", "many"])
def test_non_positive_or_non_integer_fails_at_load(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ENV, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_positive_override_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "1500")
    assert Settings(_env_file=None).api_max_tick_bar_trades == 1500


def test_dependency_reads_app_state() -> None:
    request: Any = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(max_tick_bar_trades=42))
    )
    assert get_max_tick_bar_trades(request) == 42
