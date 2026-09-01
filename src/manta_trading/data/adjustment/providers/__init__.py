"""Corporate-actions provider protocol seam (slice 128).

Mirrors the slice-127 minute-provider seam in
``manta_trading.data.historical_minute.providers``: a protocol declares the
contract, a StrEnum identifies installed implementations, and a builder
helper resolves a configured name to a concrete instance.

The seam exists with a single implementation today (``EODHDCorporateActionsProvider``).
The cost is trivial; the benefit is that adding a Polygon or AlphaVantage
CA provider in a future slice is a 3-line dispatch change rather than a
refactor.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

# Re-export the slice-127 dataclasses so the provider protocol and the
# k_factor math share one definition. Keeping a separate set here would
# guarantee drift the first time someone added a field to one and not
# the other.
from manta_trading.data.adjustment.k_factor import Dividend, Split

if TYPE_CHECKING:
    from manta_trading.config import Settings


class CorporateActionsProviderName(StrEnum):
    """Identifier for a configured corporate-actions provider.

    Mirrors ``MinuteProviderName`` from the historical-minute provider
    package. Co-located with the protocol so the configuration layer can
    reference these names without importing any concrete implementation.
    """

    EODHD = "eodhd"


class ICorporateActionsProvider(Protocol):
    """Provider contract for fetching splits and dividends per symbol.

    Implementations are responsible only for I/O and parsing — they return
    parsed records. Persistence (UPSERT into the splits/dividends tables)
    lives in :mod:`manta_trading.data.adjustment.ingest` so the
    producer/consumer split is clean and the same persister can serve any
    future CA provider.

    Failure semantics (per slice 128 §Error handling):
      * Transient failures (timeout, 5xx, 429, peer disconnect) raise
        :class:`ProviderTransientError` after retries are exhausted.
      * Permanent failures (4xx other than 429, malformed payload,
        delisted ticker) raise :class:`ProviderPermanentError`.
      * The caller (daily daemon) decides whether to block its checkpoint
        on either class. Per Decision 14, it does not.
    """

    async def fetch_splits(self, symbol: str) -> list[Split]:
        """Fetch all splits for ``symbol``.

        Args:
            symbol: ticker — provider-specific normalisation (e.g. EODHD's
                ``.US`` suffix) is the implementation's responsibility.

        Returns:
            list of :class:`Split` records, possibly empty.
        """
        ...

    async def fetch_dividends(self, symbol: str) -> list[Dividend]:
        """Fetch all dividends for ``symbol``.

        Returns:
            list of :class:`Dividend` records, possibly empty.
        """
        ...


def build_corporate_actions_provider(
    settings: Settings,
) -> ICorporateActionsProvider:
    """Resolve the configured CA provider to a concrete instance.

    Dispatches on ``settings.corporate_actions_provider`` (env var
    ``MT_CORPORATE_ACTIONS_PROVIDER``, default ``"eodhd"``). Raises
    :class:`ValueError` with a message naming valid options on an
    unrecognised name — never falls back silently.
    """
    name = settings.corporate_actions_provider

    try:
        provider_name = CorporateActionsProviderName(name)
    except ValueError as exc:
        valid = ", ".join(sorted(v.value for v in CorporateActionsProviderName))
        raise ValueError(
            f"unknown corporate_actions_provider {name!r}; valid options: {valid}"
        ) from exc

    match provider_name:
        case CorporateActionsProviderName.EODHD:
            from manta_trading.data.adjustment.providers.eodhd import (
                EODHDCorporateActionsProvider,
            )

            if not settings.eodhd_api_key:
                raise ValueError(
                    "corporate_actions_provider=eodhd requires "
                    "MT_EODHD_API_KEY to be set"
                )
            return EODHDCorporateActionsProvider(api_key=settings.eodhd_api_key)


__all__ = [
    "CorporateActionsProviderName",
    "Dividend",
    "ICorporateActionsProvider",
    "Split",
    "build_corporate_actions_provider",
]
