"""Dated regular-hours eras for the CME calendars — slice 301 D1, D2, D5.

The Globex trading day moved twice between the start of Databento's CME data
and 2020, so regular hours are dated: one :class:`HoursEra` per change, each
citing the CME document it was read from. An era is in force from its
``effective_from`` (a session date, the date the session closes) until the next
era's. The latest era of each calendar is today's hours, equal to its
``trading_calendars`` row. Eras were searched for only up to 2019-12-31 (D2).

``sunday_open`` is the open of a session whose open falls on a Sunday. It
differs from ``market_open`` only before 2012-11-19 on equity, when the trading
day began at 15:30 CT Monday to Thursday and at 17:00 CT on Sunday (SER-6465).

``CME_DATASET_START`` is the first date of Databento's CME dataset and the one
definition of where supported calendar history begins (D5). The first session
it yields is dated Monday 2010-06-07 and opens that Sunday evening.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Final

from manta_trading.market.schema.seed_cme_calendar import CME_EQUITY_CALENDAR_ID
from manta_trading.market.schema.seed_cme_metals_calendar import (
    CME_METALS_CALENDAR_ID,
)

CME_DATASET_START: Final = date(2010, 6, 6)


@dataclass(frozen=True)
class HoursEra:
    """One calendar's regular hours from ``effective_from`` on (D1)."""

    calendar_id: str
    effective_from: date
    market_open: time
    market_close: time
    sunday_open: time
    source: str


_SER_6465 = (
    "CME Special Executive Report S-6465, 2012-11-14, 'New Trading Hours: Equity "
    "Index Futures & Options on Equity Index Futures on Monday, November 19, "
    "2012': trading begins Sunday 5:00 p.m. CT and runs to 4:15 p.m. CT; re-opens "
    "5:00 p.m. Monday-Thursday for the new trade date. "
    "https://web.archive.org/web/20130412064716/http://www.cmegroup.com/rulebook/"
    "files/ser-6465_Equity_Index_Futures_Options_on_Equity_Index_Futures_20121114.pdf"
)

_GLOBEX_NOTICE_20150907 = (
    "CME Globex Notice 2015-09-07, 'Changes to Daily Maintenance Period for CME "
    "Globex': effective Monday, September 21, closing times for CME Equity, CBOT "
    "Equity, COMEX, NYMEX and DME move to 16:00 CT Monday through Friday; opens "
    "unchanged. https://web.archive.org/web/20150912161604/http://www.cmegroup.com/"
    "tools-information/lookups/advisories/electronic-trading/20150907.html"
)

CME_EQUITY_HOURS: Final[tuple[HoursEra, ...]] = (
    HoursEra(
        calendar_id=CME_EQUITY_CALENDAR_ID,
        effective_from=CME_DATASET_START,
        market_open=time(15, 30),
        market_close=time(15, 15),
        sunday_open=time(17, 0),
        source=(
            "ES contract specifications, 'MON-THURS: 5:00 p.m.-3:15 p.m. & 3:30 "
            "p.m.-4:30 p.m. (Daily maintenance shutdown 4:30 p.m.-5:00 p.m.) SUN: "
            "5:00 p.m.-3:15 p.m.' Central Time, identical 2010-04-16 to 2012-10-06; "
            "SER-6465 confirms the trading day started at 3:30 p.m. until "
            "2012-11-18. https://web.archive.org/web/20100416105946/http://"
            "www.cmegroup.com/trading/equity-index/us-index/"
            "e-mini-sandp500_contract_specifications.html"
        ),
    ),
    HoursEra(
        calendar_id=CME_EQUITY_CALENDAR_ID,
        effective_from=date(2012, 11, 19),
        market_open=time(17, 0),
        market_close=time(16, 15),
        sunday_open=time(17, 0),
        source=_SER_6465,
    ),
    HoursEra(
        calendar_id=CME_EQUITY_CALENDAR_ID,
        effective_from=date(2015, 9, 21),
        market_open=time(17, 0),
        market_close=time(16, 0),
        sunday_open=time(17, 0),
        source=_GLOBEX_NOTICE_20150907,
    ),
)

CME_METALS_HOURS: Final[tuple[HoursEra, ...]] = (
    HoursEra(
        calendar_id=CME_METALS_CALENDAR_ID,
        effective_from=CME_DATASET_START,
        market_open=time(17, 0),
        market_close=time(16, 15),
        sunday_open=time(17, 0),
        source=(
            "Gold futures contract specifications, 'CME Globex: Sunday - Friday "
            "6:00 p.m. - 5:15 p.m. (5:00 p.m. - 4:15 p.m. Chicago Time/CT) with a "
            "45-minute break each day beginning at 5:15 p.m. (4:15 p.m. CT)', "
            "identical 2010-04-02 to 2015-09-06. https://web.archive.org/web/"
            "20100402122213/http://www.cmegroup.com/trading/metals/precious/"
            "gold_contract_specifications.html"
        ),
    ),
    HoursEra(
        calendar_id=CME_METALS_CALENDAR_ID,
        effective_from=date(2015, 9, 21),
        market_open=time(17, 0),
        market_close=time(16, 0),
        sunday_open=time(17, 0),
        source=_GLOBEX_NOTICE_20150907,
    ),
)

CME_HOURS_ERAS: Final[tuple[HoursEra, ...]] = CME_EQUITY_HOURS + CME_METALS_HOURS
