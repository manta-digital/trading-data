"""Auth strategy protocol and implementations for credential resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from manta_trading.providers.types import AuthType

if TYPE_CHECKING:
    from manta_trading.config import Settings
    from manta_trading.providers.profiles import ProviderProfile


@runtime_checkable
class AuthStrategy(Protocol):
    """Credential resolution strategy for a provider."""

    def is_valid(self) -> bool: ...

    @property
    def active_source(self) -> str | None: ...

    @property
    def setup_hint(self) -> str: ...


class NoAuthStrategy:
    """No-op strategy for providers that don't require credentials."""

    def is_valid(self) -> bool:
        return True

    @property
    def active_source(self) -> str | None:
        return "none_required"

    @property
    def setup_hint(self) -> str:
        return ""


class ApiKeyAuthStrategy:
    """Resolve an API key credential from Settings."""

    def __init__(self, env_var_name: str, settings: Settings) -> None:
        self._env_var_name = env_var_name
        # Derive Settings field name: MT_ALPHAVANTAGE_API_KEY → alphavantage_api_key
        field_name = env_var_name.removeprefix("MT_").lower()
        self._credential: str | None = getattr(settings, field_name, None)

    def is_valid(self) -> bool:
        return isinstance(self._credential, str) and len(self._credential) > 0

    @property
    def active_source(self) -> str | None:
        if self.is_valid():
            return f"env:{self._env_var_name}"
        return None

    @property
    def setup_hint(self) -> str:
        return f"Set {self._env_var_name} environment variable"


def resolve_auth(profile: ProviderProfile, settings: Settings) -> AuthStrategy:
    """Construct the appropriate auth strategy for a provider profile."""
    if profile.auth_type == AuthType.NONE:
        return NoAuthStrategy()
    # AuthType.API_KEY
    return ApiKeyAuthStrategy(profile.api_key_env, settings)  # type: ignore[arg-type]
