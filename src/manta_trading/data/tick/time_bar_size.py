"""Time-bar sizes: any whole number of seconds, minutes or hours, or the session day.

Slice 309. ``parse_bar_size`` is the one grammar the bars routes and ``mt data
tick get`` accept; ``provider_schema_for`` says which held provider schema, if
any, can make a size by adding up its bars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from manta_trading.data.tick.bar_constants import PROVIDER_BAR_WIDTH_NS
from manta_trading.data.tick.constants import TickSchema

NS_PER_SECOND: Final = 1_000_000_000

#: Unit suffix to nanoseconds. Each served unit is defined here once.
BAR_SIZE_UNITS_NS: Final[dict[str, int]] = {
    "s": NS_PER_SECOND,
    "m": 60 * NS_PER_SECOND,
    "h": 3_600 * NS_PER_SECOND,
}
#: The widest intraday size; ``24h`` is allowed, anything wider is refused.
MAX_BAR_WIDTH_NS: Final = 24 * BAR_SIZE_UNITS_NS["h"]
#: The session-day token: the whole CME session, not 24 hours.
SESSION_DAY_TOKEN: Final = "1d"
#: The accepted form, quoted in every refusal.
BAR_SIZE_FORM: Final = (
    "a whole number of seconds, minutes or hours up to 24h (e.g. 1s, 15s, 90m, 4h),"
    " or 1d"
)

_PATTERN = re.compile(r"(\d+)([a-z])")


@dataclass(frozen=True)
class BarSize:
    """A parsed size. ``width_ns`` is ``None`` for the session day."""

    token: str
    width_ns: int | None
    session_day: bool


def parse_bar_size(text: str) -> BarSize:
    """Parse ``text`` (case and surrounding space ignored); ``ValueError``
    naming the accepted form when it is not one."""
    token = text.strip().lower()
    if token == SESSION_DAY_TOKEN:
        return BarSize(token, None, True)
    match = _PATTERN.fullmatch(token)
    if match is None or match.group(2) not in BAR_SIZE_UNITS_NS:
        raise ValueError(f"bar size {text!r} is not {BAR_SIZE_FORM}")
    count = int(match.group(1))
    width_ns = count * BAR_SIZE_UNITS_NS[match.group(2)]
    if count < 1 or width_ns > MAX_BAR_WIDTH_NS:
        raise ValueError(f"bar size {text!r} is not {BAR_SIZE_FORM}")
    return BarSize(f"{count}{match.group(2)}", width_ns, False)


def provider_schema_for(size: BarSize) -> TickSchema | None:
    """The widest provider schema whose bar width divides ``size`` (every one
    divides the session day); ``None`` when none does."""
    fitting = [
        (width, schema)
        for schema, width in PROVIDER_BAR_WIDTH_NS.items()
        if size.width_ns is None or size.width_ns % width == 0
    ]
    return max(fitting)[1] if fitting else None
