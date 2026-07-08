"""Survivorship-bias-free equity universe query API.

Public API:
  equity_universe, UniverseQueryError
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

__all__ = ["equity_universe", "UniverseQueryError"]

_SQL_ACTIVE = """
SELECT symbol
FROM instruments
WHERE COALESCE(first_listing_date, first_data_date) <= %(d)s
  AND (delisted_date IS NULL OR delisted_date > %(d)s)
ORDER BY symbol
"""

_SQL_UNIVERSE_MEMBERS = """
SELECT symbol
FROM universe_members
WHERE universe_name = %(u)s
  AND added_date <= %(d)s
  AND (removed_date IS NULL OR removed_date > %(d)s)
ORDER BY symbol
"""

_SQL_UNIVERSE_EXISTS = """
SELECT 1 FROM universe_members WHERE universe_name = %(u)s LIMIT 1
"""


class UniverseQueryError(RuntimeError):
    """Raised when an unknown universe name is requested."""


def equity_universe(
    conn: "psycopg.Connection[Any]",
    as_of_date: date,
    universe: str | None = None,
) -> list[str]:
    """Return symbols active on ``as_of_date``, optionally filtered to an index.

    Without ``universe``: active is defined as
      ``COALESCE(first_listing_date, first_data_date) <= as_of_date``
      AND ``(delisted_date IS NULL OR delisted_date > as_of_date)``

    With ``universe``: returns ``universe_members`` membership as of
    ``as_of_date`` directly (``added_date <= as_of_date AND
    (removed_date IS NULL OR removed_date > as_of_date)``). The
    instruments active filter is not applied — index membership is the
    authoritative lower bound for index-strategy backtests. Raises
    ``UniverseQueryError`` if the universe name has no rows at all.

    Args:
        conn: Open psycopg connection to the trading DB.
        as_of_date: The historical date to evaluate.
        universe: Optional universe name (e.g. ``'sp500'``).

    Returns:
        Sorted list of ticker symbols.

    Raises:
        UniverseQueryError: If ``universe`` is given but unknown.
    """
    params: dict[str, Any] = {"d": as_of_date}

    if universe is None:
        with conn.cursor() as cur:
            cur.execute(_SQL_ACTIVE, params)
            return [row[0] for row in cur.fetchall()]

    params["u"] = universe

    with conn.cursor() as cur:
        cur.execute(_SQL_UNIVERSE_EXISTS, params)
        if cur.fetchone() is None:
            raise UniverseQueryError(
                f"Universe {universe!r} has no rows in universe_members"
            )

        cur.execute(_SQL_UNIVERSE_MEMBERS, params)
        return [row[0] for row in cur.fetchall()]
