"""Provider-related exception hierarchy."""

from __future__ import annotations


class ProviderError(Exception):
    """Base exception for provider-related errors."""


class ProviderAuthError(ProviderError):
    """Authentication or credential errors."""


class ProviderTransientError(ProviderError):
    """Transient provider failure (timeout, 5xx, 429). Caller may retry.

    Slice 128 introduces this distinction so the daily daemon's CA-ingest
    path and the Stage B verifier can route retry-eligible failures
    differently from permanent ones.
    """


class ProviderOutcomeUnknownError(ProviderError):
    """A paid request failed in a way that does not say whether it was charged.

    Raised for a timeout, a dropped connection, a 5xx, or a mid-stream error
    on a billable call (slice 220, Technical Decision 10). The request may
    have been accepted and charged: reconcile before anything is resubmitted.
    Deliberately *not* a ``ProviderTransientError`` subclass, so no handler
    that retries transients catches it by accident.
    """


class ProviderPermanentError(ProviderError):
    """Permanent provider failure (4xx other than 429, malformed payload,
    delisted ticker). Caller should not retry; surface to the operator."""
