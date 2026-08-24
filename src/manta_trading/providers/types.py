"""Provider type enums and rate limit dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProviderType(StrEnum):
    """Registered data provider identifiers."""

    EODHD = "eodhd"
    DATABENTO = "databento"
    FLAT_FILE = "flatfile"
    KALSHI = "kalshi"


class AuthType(StrEnum):
    """Authentication strategy identifiers."""

    API_KEY = "api_key"
    NONE = "none"


@dataclass(frozen=True)
class RateLimit:
    """Provider rate limit constraints (static, from documentation)."""

    requests_per_minute: int
    daily_limit: int | None = None
