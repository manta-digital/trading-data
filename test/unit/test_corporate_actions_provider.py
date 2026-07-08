"""Tests for the ICorporateActionsProvider seam (slice 128).

Mirrors test/unit/test_provider_seam.py for the minute-provider seam.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from manta_trading.config import Settings
from manta_trading.data.adjustment.providers import (
    CorporateActionsProviderName,
    Dividend,
    ICorporateActionsProvider,
    Split,
    build_corporate_actions_provider,
)
from manta_trading.data.adjustment.providers.eodhd import (
    EODHDCorporateActionsProvider,
)


class TestEnumValues:
    def test_eodhd_value(self) -> None:
        assert CorporateActionsProviderName.EODHD.value == "eodhd"

    def test_membership_check_works(self) -> None:
        # Used by build_corporate_actions_provider for dispatch — must work
        # by enum lookup, not string comparison.
        assert CorporateActionsProviderName("eodhd") is (
            CorporateActionsProviderName.EODHD
        )


class TestSplitDividendIdentity:
    """The protocol's Split/Dividend must be the same dataclasses used by
    the slice-127 k_factor math, so the persister and the math share one
    schema."""

    def test_split_is_k_factor_split(self) -> None:
        from manta_trading.data.adjustment.k_factor import Split as KSplit
        assert Split is KSplit

    def test_dividend_is_k_factor_dividend(self) -> None:
        from manta_trading.data.adjustment.k_factor import Dividend as KDiv
        assert Dividend is KDiv


class TestBuildDispatch:
    def test_default_returns_eodhd_implementation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MT_CORPORATE_ACTIONS_PROVIDER", raising=False)
        monkeypatch.setenv("MT_EODHD_API_KEY", "test-key")
        s = Settings(_env_file=None)
        provider = build_corporate_actions_provider(s)
        assert isinstance(provider, EODHDCorporateActionsProvider)

    def test_explicit_eodhd_returns_eodhd(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT_CORPORATE_ACTIONS_PROVIDER", "eodhd")
        monkeypatch.setenv("MT_EODHD_API_KEY", "test-key")
        s = Settings(_env_file=None)
        provider = build_corporate_actions_provider(s)
        assert isinstance(provider, EODHDCorporateActionsProvider)

    def test_unknown_name_raises_with_valid_options(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT_CORPORATE_ACTIONS_PROVIDER", "polygon")
        monkeypatch.setenv("MT_EODHD_API_KEY", "test-key")
        s = Settings(_env_file=None)
        with pytest.raises(ValueError) as excinfo:
            build_corporate_actions_provider(s)
        # Error must name what's wrong AND list valid options — never
        # silent fallback.
        msg = str(excinfo.value)
        assert "polygon" in msg
        assert "eodhd" in msg

    def test_missing_credentials_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT_CORPORATE_ACTIONS_PROVIDER", "eodhd")
        monkeypatch.delenv("MT_EODHD_API_KEY", raising=False)
        s = Settings(_env_file=None)
        with pytest.raises(ValueError, match="MT_EODHD_API_KEY"):
            build_corporate_actions_provider(s)


class TestNoMagicStringsInDispatch:
    """The dispatch in build_corporate_actions_provider must go through the
    StrEnum, not raw string comparisons. We can't introspect `match` at
    runtime, but we can assert the failure mode: a hand-typed string in
    the enum that isn't in the match table would raise NotImplementedError
    or return None. Today there's only one branch, so this test
    documents the expectation for when a second arrives."""

    def test_enum_drives_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patching the enum to add a fake value would require dataclass
        # gymnastics; instead, assert that adding a member without
        # updating the match would surface in CI by checking the current
        # member list against a known set.
        members = {m.value for m in CorporateActionsProviderName}
        assert members == {"eodhd"}, (
            "Adding a CorporateActionsProviderName member requires also "
            "extending build_corporate_actions_provider's match block."
        )


class TestProtocolStructure:
    def test_eodhd_provider_implements_protocol(self) -> None:
        # Protocol conformance is structural; isinstance check works at
        # runtime when @runtime_checkable is set. Our protocol isn't
        # marked @runtime_checkable to keep it Static-Only, so we assert
        # method presence directly.
        assert hasattr(EODHDCorporateActionsProvider, "fetch_splits")
        assert hasattr(EODHDCorporateActionsProvider, "fetch_dividends")
