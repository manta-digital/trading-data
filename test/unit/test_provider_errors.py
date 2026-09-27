"""Provider error hierarchy: the outcome-unknown class (slice 220, TD 10)."""

from __future__ import annotations

import pytest

from manta_trading.providers.errors import (
    ProviderError,
    ProviderOutcomeUnknownError,
    ProviderTransientError,
)


def test_outcome_unknown_is_a_provider_error() -> None:
    assert issubclass(ProviderOutcomeUnknownError, ProviderError)


def test_outcome_unknown_is_not_transient() -> None:
    assert not issubclass(ProviderOutcomeUnknownError, ProviderTransientError)


def test_transient_handler_does_not_catch_outcome_unknown() -> None:
    """A retry-on-transient handler must let an unknown paid outcome through."""
    with pytest.raises(ProviderOutcomeUnknownError):
        try:
            raise ProviderOutcomeUnknownError("submit timed out")
        except ProviderTransientError:
            pytest.fail("ProviderTransientError handler caught an unknown outcome")
