"""The trading calendar a symbol's bars belong to (slice 174)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import psycopg


class UnknownSymbolError(ValueError):
    """``symbol`` has no instrument row: the serving API's "unknown symbol"
    (186 D5), mapped to 404. Bars stored under it cannot be dated."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        super().__init__(f"Symbol '{symbol}' not found")


@dataclass(frozen=True)
class SymbolCalendar:
    """A symbol's calendar id, its timezone and its holiday bound."""

    calendar_id: str
    zone: ZoneInfo
    holidays_seeded_through: date


def symbol_calendar(conn: psycopg.Connection[Any], symbol: str) -> SymbolCalendar:
    """The calendar of ``symbol``'s instrument, in one query.

    Raises:
        UnknownSymbolError: no instrument row has this symbol.
        ValueError: its instruments have no calendar, or disagree.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT c.calendar_id, c.timezone, c.holidays_seeded_through "
            "FROM instruments i "
            "LEFT JOIN trading_calendars c ON c.calendar_id = i.trading_calendar_id "
            "WHERE i.symbol = %s",
            (symbol,),
        )
        rows = cur.fetchall()
    if not rows:
        raise UnknownSymbolError(symbol)
    if len(rows) != 1 or rows[0][0] is None:
        raise ValueError(f"{symbol}: expected one trading calendar, found {rows}")
    calendar_id, zone, bound = rows[0]
    return SymbolCalendar(calendar_id, ZoneInfo(zone), bound)
