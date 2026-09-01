"""Sync EODHD HTTP wrapper for the slice 146 daemon path.

Centralizes outbound EODHD calls so:

  1. Every call passes through ``QuotaBucket.consume(call_type)`` (T16).
  2. 429 / Retry-After handling lives in one place (T16a).
  3. Peer-disconnect mid-send is caught and retried (T16a).

Slice 145's daemon code paths (``daily.py``, ``minute.py``,
``adjustment/providers/eodhd.py``) call into ``eodhd_get`` instead of
hand-rolling ``http.get(url)``.

This is sync because the daemon cycle functions are sync (slice 145
contract); the existing :mod:`manta_trading.api.http_retry` helpers are
async and serve the universe-rebuild path.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.acquisition.quota import CallType, QuotaBucket
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_DEFAULT_RETRY_AFTER_SECONDS: float = 60.0

_TOKEN_PATTERN = re.compile(r"(api_token=)[^&]*")


def redact_token(url: str) -> str:
    """Strip the api_token value from a URL destined for logs or errors.

    Journald persists WARNING/ERROR lines; the credential must never
    land there (found leaking during slice 916 verification).
    """
    return _TOKEN_PATTERN.sub(r"\1REDACTED", url)


class QuotaBucketMisconfiguredError(RuntimeError):
    """Raised after :data:`MAX_RETRY_COUNT` consecutive 429s on the same call.

    A correctly configured token bucket should never produce a 429.
    Repeated 429s mean the runtime budget no longer matches the
    upstream limit (operator misconfiguration, plan downgrade, etc.) —
    the runner converts this to a nonzero exit code.
    """


class QuotaBucketUnsetError(RuntimeError):
    """Raised when ``eodhd_get`` is called outside any bucket context.

    The daemon path must always throttle. Silently skipping the bucket
    would burst-fire calls and trip the upstream rate limit.
    """


@dataclass
class _Backoff:
    """Backoff for non-429 transient retries (peer disconnect, 5xx).

    Mirrors slice 145's classify_outcome transient policy: linear
    1s, 2s, 4s with a cap, retried up to ``MAX_RETRY_COUNT`` times.
    """

    backoff_seconds: tuple[float, ...] = (1.0, 2.0, 4.0)

    def wait_for(self, attempt: int) -> float:
        idx = min(attempt, len(self.backoff_seconds) - 1)
        return self.backoff_seconds[idx]


def eodhd_get(
    client: httpx.Client,
    url: str,
    call_type: CallType,
    *,
    bucket: QuotaBucket | None = None,
    sleep: Any = time.sleep,
    backoff: _Backoff | None = None,
) -> httpx.Response:
    """Perform a throttled, retry-aware GET against an EODHD URL.

    Args:
        client: Shared :class:`httpx.Client` (the cycle's existing one).
        url: Full URL including query string (api_token already attached).
        call_type: Discriminator for the call's credit cost — passed to
            ``bucket.consume`` (1 credit per /eod, 5 per /intraday,
            100 per /eod-bulk-last-day).
        bucket: Override the contextvar-resolved bucket. The runner
            sets the contextvar; standalone callers (CLI one-shots)
            may pass an explicit bucket.
        sleep: Injection seam for tests.
        backoff: Override the default retry backoff schedule.

    Returns:
        :class:`httpx.Response` for successful (2xx, 3xx, or non-429
        4xx) calls. Non-429 4xx pass through to the caller — slice 145
        classifies them at the call site.

    Raises:
        QuotaBucketUnsetError: when no bucket is in scope.
        QuotaBucketMisconfiguredError: after ``MAX_RETRY_COUNT``
            consecutive 429s on the same call.
        httpx.HTTPError: after ``MAX_RETRY_COUNT`` retries on transient
            network errors.
    """
    bucket = bucket or _resolve_bucket()
    backoff = backoff or _Backoff()
    log_url = redact_token(url)

    consecutive_429: int = 0
    transient_attempt: int = 0

    while True:
        bucket.consume(call_type)
        try:
            resp = client.get(url)
        except (
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.TimeoutException,
        ) as exc:
            # Peer disconnect mid-send. Discard the partial bytes — do
            # not parse, do not persist. The token bucket is NOT
            # refunded for the dropped call (the upstream still
            # accounted for it on their side, and refunding here would
            # overstate our remaining budget).
            transient_attempt += 1
            if transient_attempt > MAX_RETRY_COUNT:
                _logger.error(
                    "eodhd_get(%s): transient retries exhausted (%d) — raising",
                    log_url,
                    transient_attempt,
                )
                raise
            wait = backoff.wait_for(transient_attempt - 1)
            _logger.warning(
                "eodhd_get(%s): %s — retrying in %.1fs (attempt %d/%d)",
                log_url,
                type(exc).__name__,
                wait,
                transient_attempt,
                MAX_RETRY_COUNT,
            )
            sleep(wait)
            continue

        if resp.status_code == 429:
            consecutive_429 += 1
            if consecutive_429 > MAX_RETRY_COUNT:
                _logger.error(
                    "EODHD 429 escalation — token bucket likely "
                    "misconfigured (%d consecutive 429s on %s)",
                    consecutive_429,
                    log_url,
                )
                raise QuotaBucketMisconfiguredError(
                    f"{consecutive_429} consecutive 429s for {log_url}"
                )
            wait = _retry_after_seconds(resp)
            _logger.warning(
                "eodhd_get(%s): HTTP 429 — sleeping %.1fs (Retry-After=%r) "
                "(attempt %d/%d)",
                log_url,
                wait,
                resp.headers.get("Retry-After"),
                consecutive_429,
                MAX_RETRY_COUNT,
            )
            sleep(wait)
            continue

        # JSON-parse failure on a 2xx body is treated as peer-disconnect:
        # truncated/garbage payload. The cycle's classify_outcome would
        # otherwise treat the call as malformed. Slice 145's pattern
        # leaves JSON parsing to the call site, so we don't pre-parse
        # here — we just succeed and return. T16a's
        # JSONDecodeError-inside-parse-path retry classification lives
        # at the parse site (in daily/minute) where the Response is
        # consumed.
        return resp


def _retry_after_seconds(resp: httpx.Response) -> float:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return _DEFAULT_RETRY_AFTER_SECONDS
    raw = raw.strip()
    # First try: integer seconds.
    try:
        return max(0.0, float(int(raw)))
    except ValueError:
        pass
    # Second try: HTTP-date.
    try:
        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_AFTER_SECONDS
    if target is None:
        return _DEFAULT_RETRY_AFTER_SECONDS
    import datetime as _dt

    now = _dt.datetime.now(target.tzinfo or _dt.timezone.utc)
    return max(0.0, (target - now).total_seconds())


def _resolve_bucket() -> QuotaBucket:
    """Resolve the QuotaBucket from the runner's contextvar.

    Imported lazily to avoid a circular import (runner imports from
    quota; this module is imported by the runner indirectly).
    """
    from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

    bucket = QUOTA_BUCKET_VAR.get()
    if bucket is None:
        raise QuotaBucketUnsetError(
            "eodhd_get called with no QuotaBucket in scope; the runner "
            "(or the calling CLI) must set QUOTA_BUCKET_VAR before any "
            "throttled HTTP call."
        )
    return bucket
