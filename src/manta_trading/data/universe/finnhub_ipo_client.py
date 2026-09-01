"""Finnhub IPO enrichment client for the universe rebuild (slice 141).

Thin wrapper that calls FinnhubClient.fetch_profile and converts the result
into the fields the orchestrator needs: first_listing_date, venue,
trading_calendar_id. Keeps orchestrator logic out of the HTTP layer.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from manta_trading.api.finnhub.finnhubapi import FinnhubClient
from manta_trading.data.universe.venue_mapping import is_non_us_exchange
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


class FinnhubIpoClient:
    """Enriches instruments with IPO date and authoritative venue from Finnhub.

    Args:
        finnhub_client: Configured FinnhubClient instance.
        venue_mapper: Callable[[str], tuple[str, str]] mapping Finnhub exchange
            strings to (venue, trading_calendar_id). Typically venue_mapping.map_finnhub_exchange.
    """

    def __init__(
        self,
        finnhub_client: FinnhubClient,
        venue_mapper: Callable[[str], tuple[str, str]],
    ) -> None:
        self._client = finnhub_client
        self._venue_mapper = venue_mapper

    async def enrich(self, symbol: str) -> dict | None:
        """Fetch IPO date and exchange for a symbol and map to internal fields.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL').

        Returns:
            Dict with keys ``first_listing_date`` (date | None), ``venue`` (str),
            ``trading_calendar_id`` (str), ``raw_exchange`` (str — original
            Finnhub exchange string, used by the orchestrator to drop non-US
            issues), or None if Finnhub has no data.
        """
        profile = await self._client.fetch_profile(symbol)
        if profile is None:
            return None

        ipo_raw: str = profile.get("ipo", "") or ""
        first_listing_date: date | None = None
        if ipo_raw:
            try:
                first_listing_date = date.fromisoformat(ipo_raw)
            except ValueError:
                _logger.warning(
                    "FinnhubIpoClient: non-ISO ipo date %r for %s; ignoring",
                    ipo_raw,
                    symbol,
                )

        exchange_raw: str = profile.get("exchange", "") or ""
        # Skip venue-mapper warning for known non-US exchanges — the
        # orchestrator will DELETE these rows, so the warn-and-fallback
        # log line would be noise. The mapper still gets called for
        # unknown US exchanges (so genuinely-novel strings still surface).
        if is_non_us_exchange(exchange_raw):
            venue, trading_calendar_id = (
                "US",
                "NYSE",
            )  # placeholder; row will be deleted
        else:
            venue, trading_calendar_id = self._venue_mapper(exchange_raw)

        return {
            "first_listing_date": first_listing_date,
            "venue": venue,
            "trading_calendar_id": trading_calendar_id,
            "raw_exchange": exchange_raw,
        }
