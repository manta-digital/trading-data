"""Daily provider package — protocol seam + concrete implementations.

EODHD is the sole daily provider after AlphaVantage was removed in slice 152.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from manta_trading.data.acquisition.daily.provider import (
    DailyProviderName,
    IDailyDataProvider,
)

if TYPE_CHECKING:
    from manta_trading.config import Settings


def build_daily_provider(settings: "Settings") -> IDailyDataProvider:
    """Resolve the configured daily provider to a concrete instance.

    Only EODHD is supported. Raises ``ValueError`` on unrecognised name.
    """
    name = settings.daily_provider

    try:
        provider_name = DailyProviderName(name)
    except ValueError as exc:
        valid = ", ".join(sorted(v.value for v in DailyProviderName))
        raise ValueError(
            f"unknown daily_provider {name!r}; valid options: {valid}"
        ) from exc

    match provider_name:
        case DailyProviderName.EODHD:
            from manta_trading.data.acquisition.daily.providers.eodhd import (
                EODHDDailyProvider,
            )
            if not settings.eodhd_api_key:
                raise ValueError(
                    "daily_provider=eodhd requires MT_EODHD_API_KEY"
                )
            return EODHDDailyProvider(api_key=settings.eodhd_api_key)


__all__ = ["DailyProviderName", "build_daily_provider"]
