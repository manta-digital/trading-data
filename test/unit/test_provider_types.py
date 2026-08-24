"""Tests for provider types, enums, and error hierarchy."""

from __future__ import annotations

import dataclasses
from enum import StrEnum

import pytest

from manta_trading.providers.errors import ProviderAuthError, ProviderError
from manta_trading.providers.types import AuthType, ProviderType, RateLimit


class TestProviderType:
    """Verify ProviderType enum."""

    def test_is_str_enum(self):
        assert issubclass(ProviderType, StrEnum)

    def test_has_four_members(self):
        assert len(ProviderType) == 4

    def test_values_are_lowercase_strings(self):
        for member in ProviderType:
            assert member.value == member.value.lower()
            assert isinstance(member.value, str)

    def test_eodhd_serializes(self):
        assert str(ProviderType.EODHD) == "eodhd"

    def test_databento_serializes(self):
        assert str(ProviderType.DATABENTO) == "databento"

    def test_flat_file_serializes(self):
        assert str(ProviderType.FLAT_FILE) == "flatfile"

    def test_kalshi_serializes(self):
        assert str(ProviderType.KALSHI) == "kalshi"


class TestAuthType:
    """Verify AuthType enum."""

    def test_is_str_enum(self):
        assert issubclass(AuthType, StrEnum)

    def test_has_two_members(self):
        assert len(AuthType) == 2

    def test_api_key_value(self):
        assert str(AuthType.API_KEY) == "api_key"

    def test_none_value(self):
        assert str(AuthType.NONE) == "none"


class TestRateLimit:
    """Verify RateLimit frozen dataclass."""

    def test_is_frozen(self):
        rl = RateLimit(requests_per_minute=30)
        with pytest.raises(dataclasses.FrozenInstanceError):
            rl.requests_per_minute = 60  # type: ignore[misc]

    def test_daily_limit_defaults_to_none(self):
        rl = RateLimit(requests_per_minute=30)
        assert rl.daily_limit is None

    def test_daily_limit_can_be_set(self):
        rl = RateLimit(requests_per_minute=30, daily_limit=500)
        assert rl.daily_limit == 500


class TestProviderErrors:
    """Verify provider error hierarchy."""

    def test_provider_auth_error_is_subclass_of_provider_error(self):
        assert issubclass(ProviderAuthError, ProviderError)

    def test_provider_auth_error_is_subclass_of_exception(self):
        assert issubclass(ProviderAuthError, Exception)

    def test_provider_error_can_be_raised(self):
        with pytest.raises(ProviderError):
            raise ProviderError("test")

    def test_provider_auth_error_caught_by_provider_error(self):
        with pytest.raises(ProviderError):
            raise ProviderAuthError("auth failed")
