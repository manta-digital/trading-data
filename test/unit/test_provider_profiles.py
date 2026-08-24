"""Tests for provider profiles, lookup, and alias resolution."""

from __future__ import annotations

import dataclasses

import pytest

from manta_trading.data.kalshi.constants import (
    KALSHI_BASE_URL,
    KALSHI_PUBLIC_RATE_LIMIT,
)
from manta_trading.providers.profiles import (
    BUILT_IN_PROFILES,
    get_all_profiles,
    get_profile,
    resolve_alias,
)
from manta_trading.providers.types import AuthType, ProviderType


class TestProviderProfile:
    """Verify ProviderProfile frozen dataclass."""

    def test_is_frozen(self):
        profile = get_profile("databento")
        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.name = "changed"  # type: ignore[misc]


class TestBuiltInProfiles:
    """Verify BUILT_IN_PROFILES contents (152 removed AV; 261 added kalshi)."""

    def test_contains_three_entries(self):
        assert len(BUILT_IN_PROFILES) == 3

    def test_keys(self):
        assert set(BUILT_IN_PROFILES) == {"databento", "flatfile", "kalshi"}

    def test_databento_profile(self):
        p = BUILT_IN_PROFILES["databento"]
        assert p.provider_type == ProviderType.DATABENTO
        assert p.api_key_env == "MT_DATABENTO_API_KEY"
        assert p.auth_type == AuthType.API_KEY
        assert p.aliases == ("db", "bento")

    def test_flatfile_profile(self):
        p = BUILT_IN_PROFILES["flatfile"]
        assert p.provider_type == ProviderType.FLAT_FILE
        assert p.api_key_env is None
        assert p.auth_type == AuthType.NONE
        assert p.aliases == ("flat", "file")

    def test_kalshi_profile(self):
        p = BUILT_IN_PROFILES["kalshi"]
        assert p.provider_type == ProviderType.KALSHI
        assert p.base_url == KALSHI_BASE_URL
        assert p.api_key_env is None
        assert p.auth_type == AuthType.NONE
        # The shared constant object, not a copied number (one value, one source).
        assert p.rate_limit is KALSHI_PUBLIC_RATE_LIMIT


class TestGetAllProfiles:
    """Verify get_all_profiles."""

    def test_returns_all_three(self):
        profiles = get_all_profiles()
        assert len(profiles) == 3

    def test_returns_copy(self):
        profiles = get_all_profiles()
        assert profiles is not BUILT_IN_PROFILES


class TestGetProfile:
    """Verify get_profile lookup."""

    def test_returns_correct_profile(self):
        p = get_profile("databento")
        assert p.name == "databento"
        assert p.provider_type == ProviderType.DATABENTO

    def test_nonexistent_raises_key_error(self):
        with pytest.raises(KeyError, match="Available"):
            get_profile("nonexistent")

    def test_alphavantage_removed(self):
        with pytest.raises(KeyError):
            get_profile("alphavantage")


class TestResolveAlias:
    """Verify alias resolution."""

    def test_bento_resolves_to_databento(self):
        assert resolve_alias("bento") == "databento"

    def test_db_resolves_to_databento(self):
        assert resolve_alias("db") == "databento"

    def test_flat_resolves_to_flatfile(self):
        assert resolve_alias("flat") == "flatfile"

    def test_file_resolves_to_flatfile(self):
        assert resolve_alias("file") == "flatfile"

    def test_canonical_passthrough(self):
        assert resolve_alias("databento") == "databento"
        assert resolve_alias("flatfile") == "flatfile"
        assert resolve_alias("kalshi") == "kalshi"

    def test_nonexistent_raises_key_error(self):
        with pytest.raises(KeyError, match="Available"):
            resolve_alias("nonexistent")

    def test_nonexistent_error_includes_aliases(self):
        with pytest.raises(KeyError, match="Aliases"):
            resolve_alias("nonexistent")
