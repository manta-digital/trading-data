"""Exact tick prices and nanosecond times as text (slice 230, TD4).

The one place a stored fixed-point price or an epoch-nanosecond integer
becomes a string. ``get`` and ``debug`` format through these, and 231's API
does too, so the CLI and the API cannot disagree on a digit. No ``float``
anywhere: a JSON float cannot carry ``5950.25`` exactly at every magnitude,
and ``datetime`` stops at microseconds. No I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from manta_trading.data.tick.constants import TICK_PRICE_SCALE, UNDEF_INT64
from manta_trading.data.tick.tick_reads import (
    BAR_PRICE_COLUMNS,
    TICK_PRICE_COLUMNS,
    TIME_COLUMNS,
    Row,
)

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


def cell_value(name: str, value: Any) -> Any:
    """One read value with its price or time as exact text (``None`` stays
    ``None``, an undefined price becomes ``None``); anything else as stored.
    The API (231) builds its rows from this, keeping dates as dates."""
    if value is None:
        return None
    if name in TICK_PRICE_COLUMNS or name in BAR_PRICE_COLUMNS:
        return price_text(value)
    if name in TIME_COLUMNS:
        return ts_text(value)
    return value


def row_cells(columns: Sequence[str], row: Row) -> list[Any]:
    """A read row as JSON-ready values for the CLI writers: :func:`cell_value`,
    with the session date as ISO text."""
    return [
        value.isoformat()
        if name == "session_date" and value is not None
        else cell_value(name, value)
        for name, value in zip(columns, row, strict=True)
    ]
