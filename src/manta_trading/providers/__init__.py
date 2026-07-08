"""Providers package — centralized provider registry and auth."""

from __future__ import annotations

from manta_trading.providers.auth import AuthStrategy, resolve_auth
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderPermanentError,
    ProviderTransientError,
)
from manta_trading.providers.profiles import (
    ProviderProfile,
    get_all_profiles,
    get_profile,
    resolve_alias,
)
from manta_trading.providers.types import AuthType, ProviderType, RateLimit

__all__ = [
    "AuthStrategy",
    "AuthType",
    "ProviderAuthError",
    "ProviderError",
    "ProviderPermanentError",
    "ProviderTransientError",
    "ProviderProfile",
    "ProviderType",
    "RateLimit",
    "get_all_profiles",
    "get_profile",
    "resolve_alias",
    "resolve_auth",
]
