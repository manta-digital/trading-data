"""Tests for the minute-provider selection seam.

Covers ``MinuteProviderName`` enum semantics, ``build_minute_provider``
dispatch, and Pydantic-level validation that ``MT_MINUTE_PROVIDER`` rejects
unknown values at settings-load time.
"""

from __future__ import annotations

import pytest
import typer
from pydantic import ValidationError

from manta_trading.config import Settings
from manta_trading.data.historical_minute.provider import MinuteProviderName
from manta_trading.data.historical_minute.providers import (
    build_minute_provider,
)
from manta_trading.data.historical_minute.providers.eodhd import (
    EODHDMinuteProvider,
)


def _make_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str | None = None,
    eodhd_key: str | None = None,
) -> Settings:
    """Construct a Settings instance with .env disabled and explicit env vars."""
    monkeypatch.delenv("MT_MINUTE_PROVIDER", raising=False)
    monkeypatch.delenv("MT_EODHD_API_KEY", raising=False)
    if provider is not None:
        monkeypatch.setenv("MT_MINUTE_PROVIDER", provider)
    if eodhd_key is not None:
        monkeypatch.setenv("MT_EODHD_API_KEY", eodhd_key)
    return Settings(_env_file=None)


class TestMinuteProviderName:
    def test_eodhd_value(self) -> None:
        assert MinuteProviderName.EODHD.value == "eodhd"

    def test_str_compatibility(self) -> None:
        # StrEnum members are str subclasses
        assert MinuteProviderName.EODHD == "eodhd"


class TestBuildMinuteProviderDispatch:
    def test_returns_eodhd_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _make_settings(monkeypatch, eodhd_key="test-key")
        provider = build_minute_provider(settings, requests_per_minute=15)
        assert isinstance(provider, EODHDMinuteProvider)
        assert provider.max_days_per_request == 120
        assert provider.get_rate_limits().requests_per_minute == 15

    def test_missing_eodhd_key_exits_with_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        settings = _make_settings(monkeypatch)  # no key
        with pytest.raises(typer.Exit) as excinfo:
            build_minute_provider(settings, requests_per_minute=30)
        assert excinfo.value.exit_code == 1
        captured = capsys.readouterr()
        assert "MT_EODHD_API_KEY" in captured.err


class TestSettingsRejectsUnknownProvider:
    def test_unknown_value_raises_validation_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValidationError):
            _make_settings(monkeypatch, provider="bogus")
