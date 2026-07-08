"""Historical Minute Data Providers.

Selection seam: ``MinuteProviderName`` (defined alongside the protocol in
``manta_trading.data.historical_minute.provider``) plus
``build_minute_provider`` here let the rest of the code base reference
providers without any string literals or import-from-deep-paths.

Adding a new provider is a 3-line change:
1. Add the implementation module under this package.
2. Add an enum member in ``provider.py``.
3. Add a ``case`` arm in ``build_minute_provider`` that constructs it.
"""

from __future__ import annotations

import typer

from manta_trading.config import Settings
from manta_trading.data.historical_minute.provider import (
    IMinuteDataProvider,
    MinuteProviderName,
)
from manta_trading.data.historical_minute.providers.eodhd import (
    EODHDMinuteProvider,
)


def build_minute_provider(
    settings: Settings,
    *,
    requests_per_minute: int,
) -> IMinuteDataProvider:
    """Construct the minute provider selected by ``settings.minute_provider``.

    Validates that the matching API key is present in settings; raises
    ``typer.Exit(1)`` with a user-facing message if not. Pattern-matches
    on the enum to keep dispatch explicit and exhaustive.

    Args:
        settings: Loaded application settings.
        requests_per_minute: Per-minute rate-limit cap forwarded to the
            provider constructor.

    Returns:
        A ready-to-use ``IMinuteDataProvider`` instance.

    Raises:
        typer.Exit: When the credential for the selected provider is missing.
    """
    name = settings.minute_provider
    match name:
        case MinuteProviderName.EODHD:
            if not settings.eodhd_api_key:
                typer.echo(
                    "EODHD minute provider selected but MT_EODHD_API_KEY "
                    "is not set. Add it to your .env file.",
                    err=True,
                )
                raise typer.Exit(1)
            return EODHDMinuteProvider(
                api_key=settings.eodhd_api_key,
                requests_per_minute=requests_per_minute,
            )


__all__ = [
    "MinuteProviderName",
    "build_minute_provider",
]
