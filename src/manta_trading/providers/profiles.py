"""Provider profiles — frozen definitions, lookup, and alias resolution."""

from __future__ import annotations

from dataclasses import dataclass

from manta_trading.providers.types import AuthType, ProviderType, RateLimit


@dataclass(frozen=True)
class ProviderProfile:
    """Immutable configuration preset for a data provider."""

    name: str
    provider_type: ProviderType
    base_url: str | None = None
    api_key_env: str | None = None
    rate_limit: RateLimit | None = None
    aliases: tuple[str, ...] = ()
    auth_type: AuthType = AuthType.API_KEY
    description: str = ""


BUILT_IN_PROFILES: dict[str, ProviderProfile] = {
    "databento": ProviderProfile(
        name="databento",
        provider_type=ProviderType.DATABENTO,
        base_url="https://hist.databento.com",
        api_key_env="MT_DATABENTO_API_KEY",
        rate_limit=None,
        aliases=("db", "bento"),
        description="Databento historical market data",
    ),
    "flatfile": ProviderProfile(
        name="flatfile",
        provider_type=ProviderType.FLAT_FILE,
        base_url=None,
        api_key_env=None,
        rate_limit=None,
        aliases=("flat", "file"),
        auth_type=AuthType.NONE,
        description="Local flat file data source",
    ),
}


def get_all_profiles() -> dict[str, ProviderProfile]:
    """Return a copy of all built-in provider profiles."""
    return dict(BUILT_IN_PROFILES)


def get_profile(name: str) -> ProviderProfile:
    """Return a provider profile by canonical name.

    Raises ``KeyError`` with available profile names if not found.
    """
    try:
        return BUILT_IN_PROFILES[name]
    except KeyError:
        available = ", ".join(sorted(BUILT_IN_PROFILES))
        msg = f"Unknown provider {name!r}. Available: {available}"
        raise KeyError(msg) from None


def resolve_alias(name_or_alias: str) -> str:
    """Map an alias to its canonical provider name.

    Canonical names pass through unchanged. Raises ``KeyError`` with
    available names and aliases if not found.
    """
    if name_or_alias in BUILT_IN_PROFILES:
        return name_or_alias

    for canonical, profile in BUILT_IN_PROFILES.items():
        if name_or_alias in profile.aliases:
            return canonical

    available = sorted(BUILT_IN_PROFILES)
    all_aliases = sorted(
        alias
        for p in BUILT_IN_PROFILES.values()
        for alias in p.aliases
    )
    msg = (
        f"Unknown provider or alias {name_or_alias!r}. "
        f"Available: {', '.join(available)}. "
        f"Aliases: {', '.join(all_aliases)}"
    )
    raise KeyError(msg)
