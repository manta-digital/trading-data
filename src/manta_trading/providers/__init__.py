"""Providers package — centralized provider registry and auth.

``errors`` and ``types`` are leaf modules and are re-exported eagerly.
``profiles`` and ``auth`` are re-exported lazily (PEP 562): ``profiles``
imports provider constants from ``manta_trading.data.kalshi.constants``,
which itself imports ``RateLimit`` from this package — an eager re-export
here would turn that into an import cycle whenever the kalshi constants are
imported first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderPermanentError,
    ProviderTransientError,
)
from manta_trading.providers.types import AuthType, ProviderType, RateLimit

if TYPE_CHECKING:
    from manta_trading.providers.auth import AuthStrategy, resolve_auth
    from manta_trading.providers.profiles import (
        ProviderProfile,
        get_all_profiles,
        get_profile,
        resolve_alias,
    )

_LAZY_EXPORTS: dict[str, str] = {
    "AuthStrategy": "manta_trading.providers.auth",
    "resolve_auth": "manta_trading.providers.auth",
    "ProviderProfile": "manta_trading.providers.profiles",
    "get_all_profiles": "manta_trading.providers.profiles",
    "get_profile": "manta_trading.providers.profiles",
    "resolve_alias": "manta_trading.providers.profiles",
}


def __getattr__(name: str) -> Any:
    """Resolve the lazily re-exported names on first access."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


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
