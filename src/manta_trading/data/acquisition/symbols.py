"""Symbol-selector helper for the data acquisition daemon.

Replaces the legacy daemon/symbol_sources.py for the new daemon path.
Yields instruments that still need attention: active symbols and
EODHD-delisted symbols that haven't received a delisted_date yet
(so the daemon can capture their final bar on one last pass).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    import psycopg

_VALID_ORDERINGS: frozenset[str] = frozenset({"most_stale_first", "alphabetical"})


@dataclass(frozen=True)
class InstrumentRow:
    """One row yielded by iter_active_instruments."""

    symbol: str
    trading_calendar_id: str
    first_listing_date: date | None
    first_data_date: date | None
    delisted_at_eodhd: bool
    delisted_date: date | None


def iter_active_instruments(
    conn: "psycopg.Connection[object]",
    *,
    ordering: str,
    granularity: str = "daily",
) -> Generator[InstrumentRow, None, None]:
    """Yield instruments eligible for a daemon fetch cycle.

    Scope:
    - Active instruments: delisted_at_eodhd = false AND delisted_date IS NULL.
    - Newly-delisted (one final pass): delisted_at_eodhd = true AND delisted_date IS NULL.
      The daemon populates delisted_date on the first successful fetch;
      subsequent cycles exclude the symbol naturally.

    Args:
        conn:        Open psycopg connection.
        ordering:    'most_stale_first'  → ORDER BY last_attempt_ts ASC NULLS FIRST, symbol ASC
                     'alphabetical'      → ORDER BY symbol ASC  (debug / test use only)
        granularity: Granularity string used for the acquisition_state join
                     when ordering is 'most_stale_first'. Defaults to 'daily'.

    Raises:
        ValueError: If ordering is not a recognized value.
    """
    if ordering not in _VALID_ORDERINGS:
        raise ValueError(
            f"ordering must be one of {sorted(_VALID_ORDERINGS)}, got {ordering!r}"
        )

    if ordering == "most_stale_first":
        sql = """
            SELECT i.symbol,
                   i.trading_calendar_id,
                   i.first_listing_date,
                   i.first_data_date,
                   i.delisted_at_eodhd,
                   i.delisted_date
              FROM instruments i
              LEFT JOIN acquisition_state s
                ON s.symbol = i.symbol
               AND s.granularity = %s
               AND s.provider = 'eodhd'
             WHERE (i.delisted_at_eodhd = false AND i.delisted_date IS NULL)
                OR (i.delisted_at_eodhd = true  AND i.delisted_date IS NULL)
             ORDER BY s.last_attempt_ts ASC NULLS FIRST, i.symbol ASC
        """
        params: tuple = (granularity,)
    else:  # alphabetical
        sql = """
            SELECT i.symbol,
                   i.trading_calendar_id,
                   i.first_listing_date,
                   i.first_data_date,
                   i.delisted_at_eodhd,
                   i.delisted_date
              FROM instruments i
             WHERE (i.delisted_at_eodhd = false AND i.delisted_date IS NULL)
                OR (i.delisted_at_eodhd = true  AND i.delisted_date IS NULL)
             ORDER BY i.symbol ASC
        """
        params = ()

    with conn.cursor() as cur:
        cur.execute(sql, params)
        for row in cur.fetchall():
            yield InstrumentRow(
                symbol=row[0],
                trading_calendar_id=row[1],
                first_listing_date=row[2],
                first_data_date=row[3],
                delisted_at_eodhd=bool(row[4]),
                delisted_date=row[5],
            )
