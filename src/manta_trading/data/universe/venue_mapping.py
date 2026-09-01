"""Map Finnhub exchange strings to internal venue and trading_calendar_id values.

Finnhub returns verbose exchange strings like "NASDAQ NMS - GLOBAL MARKET".
This module is the single place that maintains the mapping table.
Unknown strings fall back to ('US', 'NYSE') with a warning — no silent
assignment to a wrong venue.
"""

from __future__ import annotations

from manta_trading.logging import get_logger

_logger = get_logger(__name__)

# Map Finnhub exchange string substrings → (venue, trading_calendar_id).
# Keys are checked case-insensitively as substrings of the Finnhub value.
# The first matching entry wins, so more-specific strings must appear first.
_EXCHANGE_MAP: list[tuple[str, str, str]] = [
    # NYSE ARCA (must precede NYSE to avoid partial match)
    ("nyse arca",       "NYSE_ARCA", "NYSE"),
    ("nyse mkt",        "NYSE_MKT",  "NYSE"),
    ("nyse american",   "NYSE_MKT",  "NYSE"),
    ("new york stock",  "NYSE",      "NYSE"),
    ("nyse",            "NYSE",      "NYSE"),
    # NASDAQ variants
    ("nasdaq",          "NASDAQ",    "NASDAQ"),
    # BATS / CBOE BZX
    ("bats",            "BATS",      "NYSE"),
    ("cboe bzx",        "BATS",      "NYSE"),
    ("cboe bzx",        "BATS",      "NYSE"),
]

_FALLBACK_VENUE = "US"
_FALLBACK_CALENDAR = "NYSE"

# Substrings that indicate a Finnhub-reported exchange is non-US.
# Match is case-insensitive substring. When an enrichment hit on a Finnhub
# profile yields one of these strings, the underlying instrument is a
# non-US issue (typically an ADR or cross-listing) and the orchestrator
# should DROP the row rather than fall back to venue='US'.
_NON_US_EXCHANGE_FRAGMENTS: tuple[str, ...] = (
    "toronto stock",
    "tsx venture",
    "canadian national stock",
    "neo exchange",
    "hong kong",
    "shanghai",
    "shenzhen",
    "tokyo stock",
    "osaka",
    "korea exchange",
    "taiwan stock",
    "indonesia stock",
    "singapore exchange",
    "bombay stock",
    "national stock exchange of india",
    "thailand stock",
    "kuala lumpur",
    "philippine stock",
    "australian securities",
    "asx",
    "new zealand",
    "xetra",
    "frankfurt",
    "deutsche b",
    "borsa italiana",
    "euronext",
    "london stock",
    "swiss exchange",
    "six swiss",
    "stockholm",
    "oslo",
    "copenhagen",
    "helsinki",
    "madrid",
    "athens",
    "warsaw",
    "moscow",
    "istanbul",
    "tel aviv",
    "saudi",
    "dubai",
    "qatar",
    "abu dhabi",
    "egypt",
    "nairobi",
    "johannesburg",
    "sao paulo",
    "b3 ",
    "bovespa",
    "buenos aires",
    "santiago",
    "mexico",
    "bolsa mexicana",
    "lima stock",
)


def is_non_us_exchange(exchange: str) -> bool:
    """Return True when the Finnhub exchange string indicates a non-US venue.

    Used by the Finnhub enrichment loop to drop ADRs / cross-listings whose
    underlying instrument trades on a foreign exchange. Match is
    case-insensitive substring against ``_NON_US_EXCHANGE_FRAGMENTS``.
    """
    lower = exchange.lower()
    return any(fragment in lower for fragment in _NON_US_EXCHANGE_FRAGMENTS)


def map_finnhub_exchange(exchange: str) -> tuple[str, str]:
    """Return (venue, trading_calendar_id) for a Finnhub exchange string.

    Falls back to ('US', 'NYSE') for unknown strings and logs a warning.

    Args:
        exchange: Raw exchange string from Finnhub /stock/profile2.exchange.

    Returns:
        Tuple of (venue, trading_calendar_id).
    """
    lower = exchange.lower()
    for fragment, venue, calendar in _EXCHANGE_MAP:
        if fragment in lower:
            return (venue, calendar)

    if exchange:
        _logger.warning("map_finnhub_exchange: unrecognized exchange %r; using fallback 'US'", exchange)
    return (_FALLBACK_VENUE, _FALLBACK_CALENDAR)
