"""HTTP 4xx → provider error: the one definition for every Databento path.

A 4xx means the provider refused, so nothing was charged (design Technical
Decision 10). The adapter's free and paid calls and the batch download all
map a refusal through ``refusal_error``. Imports no ``databento`` symbol.
"""

from __future__ import annotations

from http import HTTPStatus

from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderPermanentError,
    ProviderTransientError,
)

AUTH_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})


def refusal_error(status: int, detail: str) -> ProviderError:
    """429 → transient (rate-limited before any charge); 401/403 → auth;
    any other refusal → permanent."""
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return ProviderTransientError(detail)
    if status in AUTH_STATUSES:
        return ProviderAuthError(detail)
    return ProviderPermanentError(detail)
