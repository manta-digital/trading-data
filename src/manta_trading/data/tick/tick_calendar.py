"""Session days from the production calendar, for planning and adoption (224).

LLD 224 TD6: the calendar lives in the production database and only planning
(the purchase phase) and ``adopt`` read it. Every failure to obtain session
days is one :class:`TickCalendarError` naming the calendar, so a caller can
end with ``STORAGE_ABORT`` (pass) or exit 4 (adopt) and buy or write nothing.
"""

from __future__ import annotations

from datetime import date

import psycopg

from manta_trading.config import Settings
from manta_trading.data.base.trading_calendar import (
    CalendarNotFoundError,
    OutOfPopulatedRangeError,
    TradingCalendar,
)
from manta_trading.data.tick.constants import SType, calendar_for_product
from manta_trading.data.tick.session_days import session_days
from manta_trading.market.schema.databases import (
    Credential,
    Database,
    DatabaseNotConfiguredError,
    resolve_database_url,
)

#: Symbologies whose symbols begin with the product root (``ES.FUT``, ``ES.c.0``).
_ROOTED_STYPES = frozenset({SType.PARENT, SType.CONTINUOUS})
_ROOT_SEPARATOR = "."


class TickCalendarError(Exception):
    """The calendar could not give session days: nothing bought or written."""


def product_of_shape(stype_in: SType, symbols: tuple[str, ...]) -> str:
    """The futures product every symbol belongs to. ``ValueError`` when the
    symbology does not name one or the symbols span several."""
    if stype_in not in _ROOTED_STYPES:
        raise ValueError(f"stype_in {stype_in} does not name a product")
    roots = {symbol.split(_ROOT_SEPARATOR, 1)[0] for symbol in symbols}
    if len(roots) != 1:
        raise ValueError(f"symbols span products {sorted(roots)}")
    return roots.pop()


def planning_product(stype_in: SType, symbols: tuple[str, ...]) -> str:
    """:func:`product_of_shape` for planning: a shape with no product has no
    calendar, so it ends the pass like any other calendar failure."""
    try:
        return product_of_shape(stype_in, symbols)
    except ValueError as exc:
        raise TickCalendarError(f"no calendar for {list(symbols)}: {exc}") from exc


def calendar_url(settings: Settings) -> str:
    """The production database URL the calendar is read from."""
    try:
        return resolve_database_url(settings, Database.PRIMARY, Credential.APPLICATION)
    except DatabaseNotConfiguredError as exc:
        raise TickCalendarError(f"{exc.env_var} (the calendar) is not set") from exc


def product_session_days(url: str, product: str, start: date, end: date) -> list[date]:
    """The UTC days of ``[start, end)`` a session of ``product`` touches. Blocking."""
    try:
        calendar_id = calendar_for_product(product)
    except KeyError as exc:
        raise TickCalendarError(str(exc.args[0])) from exc
    calendar = TradingCalendar(calendar_id, url)
    try:
        return session_days(calendar, start, end)
    except (psycopg.OperationalError, CalendarNotFoundError) as exc:
        raise TickCalendarError(f"calendar {calendar_id} unavailable: {exc}") from exc
    except OutOfPopulatedRangeError as exc:
        raise TickCalendarError(f"calendar {calendar_id}: {exc}") from exc
    finally:
        calendar.close()
