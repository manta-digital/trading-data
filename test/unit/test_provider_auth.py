"""Tests for auth strategy protocol and implementations."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from manta_trading.providers.auth import (
    ApiKeyAuthStrategy,
    AuthStrategy,
    NoAuthStrategy,
    resolve_auth,
)
from manta_trading.providers.profiles import get_profile
from manta_trading.providers.types import AuthType


def _mock_settings(**kwargs) -> MagicMock:
    settings = MagicMock()
    settings.configure_mock(**kwargs)
    return settings


class TestNoAuthStrategy:
    def test_is_always_valid(self):
        assert NoAuthStrategy().is_valid() is True

    def test_active_source(self):
        assert NoAuthStrategy().active_source == "none_required"

    def test_setup_hint_is_empty(self):
        assert NoAuthStrategy().setup_hint == ""

    def test_satisfies_protocol(self):
        assert isinstance(NoAuthStrategy(), AuthStrategy)


class TestApiKeyAuthStrategy:
    def test_valid_with_credential(self):
        settings = _mock_settings(eodhd_api_key="demo-key")
        strategy = ApiKeyAuthStrategy("MT_EODHD_API_KEY", settings)
        assert strategy.is_valid() is True

    def test_active_source_with_credential(self):
        settings = _mock_settings(eodhd_api_key="demo-key")
        strategy = ApiKeyAuthStrategy("MT_EODHD_API_KEY", settings)
        assert strategy.active_source == "env:MT_EODHD_API_KEY"

    def test_invalid_without_credential(self):
        settings = MagicMock(spec=[])
        strategy = ApiKeyAuthStrategy("MT_EODHD_API_KEY", settings)
        assert strategy.is_valid() is False

    def test_active_source_none_without_credential(self):
        settings = MagicMock(spec=[])
        strategy = ApiKeyAuthStrategy("MT_EODHD_API_KEY", settings)
        assert strategy.active_source is None

    def test_invalid_with_empty_string(self):
        settings = _mock_settings(eodhd_api_key="")
        strategy = ApiKeyAuthStrategy("MT_EODHD_API_KEY", settings)
        assert strategy.is_valid() is False

    def test_setup_hint_contains_env_var(self):
        settings = MagicMock(spec=[])
        strategy = ApiKeyAuthStrategy("MT_EODHD_API_KEY", settings)
        assert "MT_EODHD_API_KEY" in strategy.setup_hint

    def test_satisfies_protocol(self):
        settings = _mock_settings(eodhd_api_key="key")
        assert isinstance(
            ApiKeyAuthStrategy("MT_EODHD_API_KEY", settings),
            AuthStrategy,
        )


class TestResolveAuth:
    def test_none_auth_returns_no_auth_strategy(self):
        profile = get_profile("flatfile")
        settings = MagicMock()
        result = resolve_auth(profile, settings)
        assert isinstance(result, NoAuthStrategy)

    def test_api_key_auth_returns_api_key_strategy(self):
        profile = get_profile("databento")
        settings = _mock_settings(databento_api_key="demo")
        result = resolve_auth(profile, settings)
        assert isinstance(result, ApiKeyAuthStrategy)

    def test_api_key_strategy_reads_correct_credential(self):
        profile = get_profile("databento")
        settings = _mock_settings(databento_api_key="my-key")
        result = resolve_auth(profile, settings)
        assert result.is_valid() is True
        assert result.active_source == "env:MT_DATABENTO_API_KEY"
