"""Exact tick prices and nanosecond times as text (slice 230, TD4).

The one place a stored fixed-point price or an epoch-nanosecond integer
becomes a string. ``get`` and ``debug`` format through these, and 231's API
does too, so the CLI and the API cannot disagree on a digit. No ``float``
anywhere: a JSON float cannot carry ``5950.25`` exactly at every magnitude,
and ``datetime`` stops at microseconds. No I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from manta_trading.data.tick.constants import TICK_PRICE_SCALE, UNDEF_INT64

_NS_PER_SECOND = 1_000_000_000
_DIGITS = len(str(TICK_PRICE_SCALE)) - 1  # the scale's power of ten


def price_text(raw: int) -> str | None:
    """``5950250000000`` → ``"5950.25"``; ``UNDEF_INT64`` → ``None``.

    Trailing zeros are dropped, but the result is never in exponent form.
    """
    if raw == UNDEF_INT64:
        return None
    return format(Decimal(raw).scaleb(-_DIGITS).normalize(), "f")


def ts_text(ns: int) -> str:
    """Epoch nanoseconds → ``2024-09-16T13:30:00.123456789Z`` (always nine
    fraction digits)."""
    seconds, fraction = divmod(ns, _NS_PER_SECOND)
    whole = datetime.fromtimestamp(seconds, UTC)
    return f"{whole:%Y-%m-%dT%H:%M:%S}.{fraction:09d}Z"
