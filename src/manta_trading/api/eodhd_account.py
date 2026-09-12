"""EODHD account status: how much of the day's allowance is spent.

One endpoint, one call, read-only. Deliberately separate from
``eodhd_sync.eodhd_get``, which every data fetch goes through: that function
consumes from the quota bucket, and asking how many credits are left must not
itself spend one. It also throttles and retries, which is right for a fetch
worth waiting for and wrong for a status line.

``apiRequests`` is credits **used**, not remaining — the field name suggests a
count of requests, but on a plan where an ``/intraday`` call costs five it
tracks credits. The overview prints it as used against the plan's daily
allowance.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from manta_trading.api.eodhd_sync import redact_token
from manta_trading.constants import (
    EODHD_ACCOUNT_TIMEOUT_SECONDS,
    EODHD_USER_ENDPOINT,
)


@dataclass(frozen=True)
class CreditUsage:
    """The day's EODHD credit position.

    Attributes:
        used: Credits spent so far today (the endpoint's ``apiRequests``).
        daily_limit: The plan's daily allowance (``dailyRateLimit``).
        extra: Purchased credits beyond the daily allowance (``extraLimit``),
            which are what let a pass keep running past the plan limit.
    """

    used: int
    daily_limit: int
    extra: int

    @property
    def remaining(self) -> int:
        """Credits still available today, extras included."""
        return self.daily_limit + self.extra - self.used


def fetch_credit_usage(
    api_key: str, *, client: httpx.Client | None = None
) -> CreditUsage:
    """Read the account's credit position. One GET, bounded by a short timeout.

    Args:
        api_key: ``MT_EODHD_API_KEY``.
        client: Optional client, for tests. A fresh one is used otherwise;
            the overview makes exactly one call, so a shared client would buy
            nothing.

    Returns:
        The parsed usage.

    Raises:
        httpx.HTTPError: on a non-2xx response, a timeout, or a network
            failure. The caller decides what an unanswered question means;
            the overview turns it into an "unavailable" line and still
            exits 0.
        KeyError, ValueError: if the payload does not carry the three
            integer fields. A shape that changed silently would otherwise
            print a plausible wrong number.
    """
    url = f"{EODHD_USER_ENDPOINT}?api_token={api_key}&fmt=json"
    if client is not None:
        response = client.get(url, timeout=EODHD_ACCOUNT_TIMEOUT_SECONDS)
    else:
        response = httpx.get(url, timeout=EODHD_ACCOUNT_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    try:
        return CreditUsage(
            used=int(payload["apiRequests"]),
            daily_limit=int(payload["dailyRateLimit"]),
            extra=int(payload.get("extraLimit", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # Never guess at a credit number: a wrong one is worse than none.
        raise ValueError(f"unexpected payload from {redact_token(url)}: {exc}") from exc
