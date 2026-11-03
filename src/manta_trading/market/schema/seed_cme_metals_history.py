"""Pre-2020 ``CME_METALS`` exceptions, 2010-06-06 to 2019-12-31 — slice 301 D4.

One row per weekday that did not trade the era's regular session (see
``seed_cme_hours``), each read from the metals product-group row ("NYMEX, COMEX and
DME", "Energy, Metals & DME" and their variants) of a
dated CME document and citing it. Nothing is generated from a holiday rule or
copied from the other CME calendar. Sessions are dated by the day they close,
so a holiday halt is an early close on the holiday itself (221 D4).
Times are America/Chicago as the documents state them.

Every metals reopen in the range is at the regular 17:00, so there are no late
opens. Fridays before some 2010-2011 holidays close early at 15:15.

Kept out of ``seed_cme_metals_calendar``, which holds 2020 onward and is
already past the size guideline: this module is data.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, Final

from manta_trading.market.schema.seed_cme_calendar import (
    closed_day,
    early_close_day,
)

# Wayback snapshots of CME's yearly Globex holiday-calendar zips, and of the
# notices for unscheduled closures; the live cmegroup.com refuses automated reads.
CME_METALS_HISTORY_EXCEPTIONS: Final[tuple[dict[str, Any], ...]] = (
    # source: CME Globex holiday schedule 2010-4th-of-july.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 7, 2), "Friday before Independence Day", time(15, 15)),
    # source: CME Globex holiday schedule 2010-4th-of-july.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 7, 5), "Independence Day (observed)", time(12, 15)),
    # source: CME Globex holiday schedule 2010-labor-day.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 9, 3), "Friday before Labor Day", time(15, 15)),
    # source: CME Globex holiday schedule 2010-labor-day.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 9, 6), "Labor Day", time(12, 15)),
    # source: CME Globex holiday schedule 2010-columbus-day.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 10, 8), "Friday before Columbus Day", time(15, 15)),
    # source: CME Globex holiday schedule 2010-thanksgiving.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 11, 25), "Thanksgiving Day", time(12, 15)),
    # source: CME Globex holiday schedule 2010-thanksgiving.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 11, 26), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2010-christmas.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    closed_day(date(2010, 12, 24), "Christmas Day (observed)"),
    # source: CME Globex holiday schedule 2011-new-years.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 12, 31), "New Year's Eve", time(15, 15)),
    # source: CME Globex holiday schedule 2011-martin-luther-king-done.pdf (in the
    # year's holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row,
    # via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(
        date(2011, 1, 14),
        "Friday before Martin Luther King Jr. Day",
        time(15, 15),
    ),
    # source: CME Globex holiday schedule 2011-martin-luther-king-done.pdf (in the
    # year's holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row,
    # via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 1, 17), "Martin Luther King Jr. Day", time(12, 15)),
    # source: CME Globex holiday schedule 2011-presidents-day-done.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 2, 21), "Presidents Day", time(12, 15)),
    # source: CME Globex holiday schedule 2011-good-friday-done.pdf (in the year's
    # holiday-calendars.zip), NYMEX & COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    closed_day(date(2011, 4, 22), "Good Friday"),
    # source: CME Globex holiday schedule 2011-memorial-day-done.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 5, 30), "Memorial Day", time(12, 15)),
    # source: CME Globex holiday schedule 2011-4th-of-july-done.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 7, 4), "Independence Day", time(12, 15)),
    # source: CME Globex holiday schedule 2011-labor-day-done.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 9, 5), "Labor Day", time(12, 15)),
    # source: CME Globex holiday schedule 2011-thanksgiving-done.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 11, 24), "Thanksgiving Day", time(12, 15)),
    # source: CME Globex holiday schedule 2011-thanksgiving-done.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 11, 25), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2011-christmas  (2).pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    closed_day(date(2011, 12, 26), "Christmas Day (observed)"),
    # source: CME Globex holiday schedule 2012-new-years.doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    closed_day(date(2012, 1, 2), "New Year's Day (observed)"),
    # source: CME Globex holiday schedule 2012-martin-luther-king-done.docx (in the
    # year's holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row,
    # via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 1, 16), "Martin Luther King Jr. Day", time(12, 15)),
    # source: CME Globex holiday schedule 2012-presidents-day-done.doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 2, 20), "Presidents Day", time(12, 15)),
    # source: CME Globex holiday schedule 2012-good-friday-done.doc (in the year's
    # holiday-calendars.zip), NYMEX & COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    closed_day(date(2012, 4, 6), "Good Friday"),
    # source: CME Globex holiday schedule 2012-memorial-day-done.doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 5, 28), "Memorial Day", time(12, 15)),
    # source: CME Globex holiday schedule 2012-4th-of-july-done.doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 7, 4), "Independence Day", time(12, 15)),
    # source: CME Globex holiday schedule 2012-labor-day-done.doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 9, 3), "Labor Day", time(12, 15)),
    # source: CME Globex holiday schedule 2012-thanksgiving-done.doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 11, 22), "Thanksgiving Day", time(12, 15)),
    # source: CME Globex holiday schedule 2012-thanksgiving-done.doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 11, 23), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2012-christmas .doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME Globex holiday schedule 2012-christmas .doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    closed_day(date(2012, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2013-new-years.doc (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and DME Products on CME Globex row, via
    # Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    closed_day(date(2013, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2013-martin-luther-king.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX & Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 1, 21), "Martin Luther King Jr. Day", time(12, 15)),
    # source: CME Globex holiday schedule 2013-presidents-day.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX & Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 2, 18), "Presidents Day", time(12, 15)),
    # source: CME Globex holiday schedule 2013-good-friday.pdf (in the year's
    # holiday-calendars.zip), NYMEX & COMEX and Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    closed_day(date(2013, 3, 29), "Good Friday"),
    # source: CME Globex holiday schedule 2013-memorial-day.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX & Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 5, 27), "Memorial Day", time(12, 15)),
    # source: CME Globex holiday schedule 2013-4th-of-july-done.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX & Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 7, 4), "Independence Day", time(12, 15)),
    # source: CME Globex holiday schedule 2013-labor-day.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX & Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 9, 2), "Labor Day", time(12, 15)),
    # source: CME Globex holiday schedule 2013-thanksgiving.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 11, 28), "Thanksgiving Day", time(12, 15)),
    # source: CME Globex holiday schedule 2013-thanksgiving.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX and Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 11, 29), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2013-christmas.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX & Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME Globex holiday schedule 2013-christmas.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX & Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    closed_day(date(2013, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2014-new-years.pdf (in the year's
    # holiday-calendars.zip), NYMEX, COMEX & Dubai Mercantile (DME) Products row, via
    # Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    closed_day(date(2014, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2014-martin-luther-king-holiday-schedule.pdf
    # (in the year's holiday-calendars.zip), Energy, Metals & DME Products row, via
    # Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 1, 20), "Martin Luther King Jr. Day", time(12, 15)),
    # source: CME Globex holiday schedule 2014-presidents-day-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 2, 17), "Presidents Day", time(12, 15)),
    # source: CME Globex holiday schedule 2014-good-friday-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    closed_day(date(2014, 4, 18), "Good Friday"),
    # source: CME Globex holiday schedule 2014-memorial-day-holiday-schedule.pdf (in the
    # year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 5, 26), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2014-4th-of-july-holiday-schedule.pdf (in the
    # year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2014-labor-day-holiday-schedule.pdf (in the
    # year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 9, 1), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2014-thanksgiving-holiday-schedule.pdf (in the
    # year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 11, 27), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2014-thanksgiving-holiday-schedule.pdf (in the
    # year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 11, 28), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2014-christmas-holiday-schedule.pdf (in the
    # year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME Globex holiday schedule 2014-christmas-holiday-schedule.pdf (in the
    # year's holiday-calendars.zip), Energy, Metals & DME Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    closed_day(date(2014, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2015-new-years-holiday-schedule.xls (in the
    # 2014 holiday-calendars.zip), "Energy, Metals & DME Products (see notes below)"
    # row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    closed_day(date(2015, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2015-martin-luther-king-holiday-schedule.xls
    # (in the 2015 holiday-calendars.zip), "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 1, 19), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-presidents-day-holiday-schedule.xls (in
    # the 2015 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 2, 16), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-good-friday-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    closed_day(date(2015, 4, 3), "Good Friday"),
    # source: CME Globex holiday schedule 2015-memorial-day-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 5, 25), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-4th-of-july-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 7, 3), "Independence Day (observed)", time(12, 0)),
    # source: CME Globex holiday schedule 2015-labor-day-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 9, 7), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-thanksgiving-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 11, 26), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-thanksgiving-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 11, 27), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2015-christmas-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME products (see notes below)"
    # row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME Globex holiday schedule 2015-christmas-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME products (see notes below)"
    # row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    closed_day(date(2015, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2016-new-years-holiday-schedule.xls (in the
    # 2015 holiday-calendars.zip), "Energy, Metals & DME Products (see notes below)"
    # row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    closed_day(date(2016, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2016-martin-luther-king-holiday-schedule.xls
    # (in the 2016 holiday-calendars.zip), "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 1, 18), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-presidents-day-holiday-schedule.xls (in
    # the 2016 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 2, 15), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-good-friday-holiday-schedule.xls (in the
    # 2016 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    closed_day(date(2016, 3, 25), "Good Friday"),
    # source: CME Globex holiday schedule 2016-memorial-day-holiday-schedule.xls (in the
    # 2016 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 5, 30), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-4th-of-july-holiday-schedule.xls (in the
    # 2016 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-labor-day-holiday-schedule.xls (in the
    # 2016 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 9, 5), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-thanksgiving-holiday-schedule.xls (in the
    # 2016 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 11, 24), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-thanksgiving-holiday-schedule.xls (in the
    # 2016 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 11, 25), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2016-christmas-holiday-schedule.xls (in the
    # 2016 holiday-calendars.zip), "Energy, Metals & DME products (see notes below)"
    # row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    closed_day(date(2016, 12, 26), "Christmas Day (observed)"),
    # source: CME Globex holiday schedule 2017-new-years-holiday-schedule.xls (in the
    # 2016 holiday-calendars.zip), "Energy, Metals & DME Products (see notes below)"
    # row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    closed_day(date(2017, 1, 2), "New Year's Day (observed)"),
    # source: CME Globex holiday schedule 2017-martin-luther-king-holiday-schedule.xls
    # (in the 2017 holiday-calendars.zip), "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 1, 16), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-presidents-day-holiday-schedule.xls (in
    # the 2017 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 2, 20), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-good-friday-holiday-schedule.xls (in the
    # 2017 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    closed_day(date(2017, 4, 14), "Good Friday"),
    # source: CME Globex holiday schedule 2017-memorial-day-holiday-schedule.xls (in the
    # 2017 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 5, 29), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-4th-of-july-holiday-schedule.xls (in the
    # 2017 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-labor-day-holiday-schedule.xls (in the
    # 2017 holiday-calendars.zip), "Energy, Metals & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 9, 4), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-thanksgiving-holiday-schedule.xls (in the
    # 2017 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 11, 23), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-thanksgiving-holiday-schedule.xls (in the
    # 2017 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 11, 24), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2017-christmas-holiday-schedule.xls (in the
    # 2017 holiday-calendars.zip), "Energy, Metals, Softs & DME products (see notes
    # below)" row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    closed_day(date(2017, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2018-new-years-holiday-schedule.xls (in the
    # 2017 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    closed_day(date(2018, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2018-martin-luther-king-holiday-schedule.xls
    # (in the 2018 holiday-calendars.zip), "Energy, Metal, Softs & DME Products" row,
    # via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 1, 15), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-presidents-day-holiday-schedule.xls (in
    # the 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 2, 19), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-good-friday-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    closed_day(date(2018, 3, 30), "Good Friday"),
    # source: CME Globex holiday schedule 2018-memorial-day-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 5, 28), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-4th-of-july-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-labor-day-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 9, 3), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-thanksgiving-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 11, 22), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-thanksgiving-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 11, 23), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2018-christmas-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME products (see notes
    # below)" row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME Globex holiday schedule 2018-christmas-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME products (see notes
    # below)" row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    closed_day(date(2018, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2019-new-years-holiday-schedule.xls (in the
    # 2018 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    closed_day(date(2019, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2019-mlk-day-schedule.xls (in the 2019
    # holiday-calendars.zip), "Energy, Metal, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 1, 21), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-presidents-day-schedule.xls (in the 2019
    # holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 2, 18), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-good-friday-schedule.xls (in the 2019
    # holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    closed_day(date(2019, 4, 19), "Good Friday"),
    # source: CME Globex holiday schedule 2019-memorial-day-schedule.xls (in the 2019
    # holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 5, 27), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-independence-day-schedule.xls (in the
    # 2019 holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-labor-day-schedule.xls (in the 2019
    # holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 9, 2), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-thanksgiving-schedule.xls (in the 2019
    # holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 11, 28), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-thanksgiving-schedule.xls (in the 2019
    # holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 11, 29), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday schedule 2019-christmas-holiday-schedule.xls (in the
    # 2019 holiday-calendars.zip), "Energy, Metals, Softs & DME products (see notes
    # below)" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME Globex holiday schedule 2019-christmas-holiday-schedule.xls (in the
    # 2019 holiday-calendars.zip), "Energy, Metals, Softs & DME products (see notes
    # below)" row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    closed_day(date(2019, 12, 25), "Christmas Day"),
)
