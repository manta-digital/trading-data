"""An unknown ``MT_TICK_DATABENTO_PLAN`` is refused before a provider call (300)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from manta_trading.cli.app import app


def test_the_tick_pass_is_refused_before_a_provider_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def provider_must_not_be_built(*args: object) -> None:
        raise AssertionError("a provider was built")

    monkeypatch.setattr(
        "manta_trading.data.tick.run_context._default_provider",
        provider_must_not_be_built,
    )
    monkeypatch.setenv("MT_TICK_DATABENTO_PLAN", "platinum")
    result = CliRunner().invoke(app, ["data", "tick", "pass", "--estimate-only"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationError)
    assert "MT_TICK_DATABENTO_PLAN" in str(result.exception)
    assert "standard" in str(result.exception)
