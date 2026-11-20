"""The session-aligned bar statement (slice 174 D1, D2).

The phase intervals come from ``session_phases.phases_for`` and arrive as
arrays, so the phase rule has one definition. The statement only buckets:
``date_bin(width, time, phase_start)`` is ``phase_start + floor((time -
phase_start) / width) * width``, so each phase is bucketed from its own start
and no bar crosses a phase. Prices and volume are aggregated as the eligible
rollups aggregate them (``eligible_ohlcv_select``); ``prints=all`` reads the
raw columns. A bucket with no eligible price has no bar.

``session_date`` comes from the arrays, never from casting a timestamp: the
production session timezone is America/Denver.
"""

from __future__ import annotations

from typing import Final

from manta_trading.constants import MINUTE_OHLCV_TABLE, PrintMode
from manta_trading.market.schema.eligible_rollups_sql import eligible_ohlcv_select
from manta_trading.market.schema.eligible_sql import has_eligible_price

_ALIAS: Final = "m"
_INDENT: Final = " " * 4

_RAW_OHLCV: Final = f",\n{_INDENT}".join(
    (
        f"FIRST({_ALIAS}.open, {_ALIAS}.time) AS open",
        f"MAX({_ALIAS}.high) AS high",
        f"MIN({_ALIAS}.low) AS low",
        f"LAST({_ALIAS}.close, {_ALIAS}.time) AS close",
        f"SUM({_ALIAS}.volume) AS volume",
    )
)

SESSION_BAR_COLUMNS: Final = (
    "bucket",
    "session_date",
    "phase",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
"""The statement's columns, in order."""


def session_bars_sql(prints: PrintMode) -> str:
    """The statement; parameters ``symbol``, ``width`` (interval), ``lo``/``hi``
    (the outer time bounds, for chunk exclusion) and the phase arrays
    ``dates``, ``phases``, ``starts``, ``ends``."""
    if prints is PrintMode.ALL:
        ohlcv, having = _RAW_OHLCV, ""
    else:
        ohlcv = eligible_ohlcv_select(_ALIAS, _INDENT)
        having = f"HAVING bool_or({has_eligible_price(_ALIAS)})"
    return f"""
WITH p (session_date, phase, phase_start, phase_end) AS (
    SELECT * FROM unnest(
        %(dates)s::date[], %(phases)s::text[],
        %(starts)s::timestamptz[], %(ends)s::timestamptz[]
    )
)
SELECT
    date_bin(%(width)s::interval, {_ALIAS}.time, p.phase_start) AS bucket,
    p.session_date,
    p.phase,
    {ohlcv}
FROM p
JOIN {MINUTE_OHLCV_TABLE} {_ALIAS}
  ON {_ALIAS}.time >= p.phase_start AND {_ALIAS}.time < p.phase_end
WHERE {_ALIAS}.symbol = %(symbol)s
  AND {_ALIAS}.time >= %(lo)s AND {_ALIAS}.time < %(hi)s
GROUP BY p.session_date, p.phase, p.phase_start, bucket
{having}
ORDER BY bucket
"""
