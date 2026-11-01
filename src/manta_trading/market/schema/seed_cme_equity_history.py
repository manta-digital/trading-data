"""Pre-2020 ``CME_EQUITY`` exceptions, 2010-06-06 to 2019-12-31 — slice 301 D4.

One row per weekday that did not trade the era's regular session (see
``seed_cme_hours``), each read from the equity product-group row ("CME Group Equity
Products", "Equity Products") of a
dated CME document and citing it. Nothing is generated from a holiday rule or
copied from the other CME calendar. Sessions are dated by the day they close,
so a holiday halt is an early close on the holiday itself (221 D4).
Times are America/Chicago as the documents state them.

Before 2012-11-19 the trading day began at 15:30 CT, so a session that reopens at
17:00 after a holiday halt carries a late open (``late_open_day``). Late opens at
05:00 after Christmas and New Year (2011-12-27 to 2014-01-02) are as the documents
state. Unscheduled closures (Hurricane Sandy 2012-10-29..31, the 2018-12-05 day of
mourning) cite CME's notices, not the holiday zips.

Kept out of ``seed_cme_calendar``, which holds 2020 onward and is
already past the size guideline: this module is data.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, Final

from manta_trading.market.schema.seed_cme_calendar import (
    closed_day,
    early_close_day,
    late_open_day,
)

# Wayback snapshots of CME's yearly Globex holiday-calendar zips, and of the
# notices for unscheduled closures; the live cmegroup.com refuses automated reads.
CME_EQUITY_HISTORY_EXCEPTIONS: Final[tuple[dict[str, Any], ...]] = (
    # source: CME Globex holiday schedule 2010-4th-of-july.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 7, 5), "Independence Day (observed)", time(10, 30)),
    # source: CME Globex holiday schedule 2010-4th-of-july.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    late_open_day(
        date(2010, 7, 6),
        "Day after Independence Day (observed)",
        time(17, 0),
    ),
    # source: CME Globex holiday schedule 2010-labor-day.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 9, 6), "Labor Day", time(10, 30)),
    # source: CME Globex holiday schedule 2010-labor-day.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    late_open_day(date(2010, 9, 7), "Day after Labor Day", time(17, 0)),
    # source: CME Globex holiday schedule 2010-thanksgiving.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    early_close_day(date(2010, 11, 25), "Thanksgiving Day", time(10, 30)),
    # source: CME Globex holiday schedule 2010-thanksgiving.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback: BOTH late open
    # and early close:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    late_open_day(
        date(2010, 11, 26),
        "Day after Thanksgiving",
        time(17, 0),
        close=time(12, 15),
    ),
    # source: CME Globex holiday schedule 2010-christmas.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115305/http://www.cmegroup.com/tools-information/holiday-calendar/files/2010-holiday-calendars.zip
    closed_day(date(2010, 12, 24), "Christmas Day (observed)"),
    # source: CME Globex holiday schedule 2011-martin-luther-king-done.pdf (in the
    # year's holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 1, 17), "Martin Luther King Jr. Day", time(10, 30)),
    # source: CME Globex holiday schedule 2011-martin-luther-king-done.pdf (in the
    # year's holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    late_open_day(
        date(2011, 1, 18),
        "Day after Martin Luther King Jr. Day",
        time(17, 0),
    ),
    # source: CME Globex holiday schedule 2011-presidents-day-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 2, 21), "Presidents Day", time(10, 30)),
    # source: CME Globex holiday schedule 2011-presidents-day-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    late_open_day(date(2011, 2, 22), "Day after Presidents Day", time(17, 0)),
    # source: CME Globex holiday schedule 2011-good-friday-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    closed_day(date(2011, 4, 22), "Good Friday"),
    # source: CME Globex holiday schedule 2011-memorial-day-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 5, 30), "Memorial Day", time(10, 30)),
    # source: CME Globex holiday schedule 2011-memorial-day-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    late_open_day(date(2011, 5, 31), "Day after Memorial Day", time(17, 0)),
    # source: CME Globex holiday schedule 2011-4th-of-july-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 7, 4), "Independence Day", time(10, 30)),
    # source: CME Globex holiday schedule 2011-4th-of-july-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    late_open_day(date(2011, 7, 5), "Day after Independence Day", time(17, 0)),
    # source: CME Globex holiday schedule 2011-labor-day-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 9, 5), "Labor Day", time(10, 30)),
    # source: CME Globex holiday schedule 2011-labor-day-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    late_open_day(date(2011, 9, 6), "Day after Labor Day", time(17, 0)),
    # source: CME Globex holiday schedule 2011-thanksgiving-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    early_close_day(date(2011, 11, 24), "Thanksgiving Day", time(10, 30)),
    # source: CME Globex holiday schedule 2011-thanksgiving-done.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback: BOTH late open
    # and early close:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    late_open_day(
        date(2011, 11, 25),
        "Day after Thanksgiving",
        time(17, 0),
        close=time(12, 15),
    ),
    # source: CME Globex holiday schedule 2011-christmas  (2).pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    closed_day(date(2011, 12, 26), "Christmas Day (observed)"),
    # source: CME Globex holiday schedule 2011-christmas  (2).pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    late_open_day(date(2011, 12, 27), "Day after Christmas Day (observed)", time(5, 0)),
    # source: CME Globex holiday schedule 2012-new-years.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    closed_day(date(2012, 1, 2), "New Year's Day (observed)"),
    # source: CME Globex holiday schedule 2012-new-years.pdf (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115411/http://www.cmegroup.com/tools-information/holiday-calendar/files/2011-holiday-calendars.zip
    late_open_day(date(2012, 1, 3), "Day after New Year's Day (observed)", time(5, 0)),
    # source: CME Globex holiday schedule 2012-martin-luther-king-done.docx (in the
    # year's holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 1, 16), "Martin Luther King Jr. Day", time(10, 30)),
    # source: CME Globex holiday schedule 2012-martin-luther-king-done.docx (in the
    # year's holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    late_open_day(
        date(2012, 1, 17),
        "Day after Martin Luther King Jr. Day",
        time(17, 0),
    ),
    # source: CME Globex holiday schedule 2012-presidents-day-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 2, 20), "Presidents Day", time(10, 30)),
    # source: CME Globex holiday schedule 2012-presidents-day-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    late_open_day(date(2012, 2, 21), "Day after Presidents Day", time(17, 0)),
    # source: CME Globex holiday schedule 2012-good-friday-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 4, 6), "Good Friday", time(8, 15)),
    # source: CME Globex holiday schedule 2012-memorial-day-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 5, 28), "Memorial Day", time(10, 30)),
    # source: CME Globex holiday schedule 2012-memorial-day-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    late_open_day(date(2012, 5, 29), "Day after Memorial Day", time(17, 0)),
    # source: CME Globex holiday schedule 2012-4th-of-july-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME Globex holiday schedule 2012-4th-of-july-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 7, 4), "Independence Day", time(10, 30)),
    # source: CME Globex holiday schedule 2012-4th-of-july-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    late_open_day(date(2012, 7, 5), "Day after Independence Day", time(17, 0)),
    # source: CME Globex holiday schedule 2012-labor-day-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 9, 3), "Labor Day", time(10, 30)),
    # source: CME Globex holiday schedule 2012-labor-day-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    late_open_day(date(2012, 9, 4), "Day after Labor Day", time(17, 0)),
    # source: CME Group media alert, 2012-10-28, "CME Group to Close U.S. Equity Index
    # Futures and Options on Futures Monday in Coordination with the U.S. Equities
    # Markets": equity index markets close on CME Globex at 8:15 a.m. CT Monday, Oct. 29
    # (not on Wayback; PR Newswire copy):
    # https://prnewswire.com/news-releases/media-alert-cme-group-to-close-us-equity-index-futures-and-options-on-futures-monday-in-coordination-with-the-us-equities-markets-176204641.html
    early_close_day(date(2012, 10, 29), "Hurricane Sandy", time(8, 15)),
    # source: CME Clearing advisory 12-464, 2012-10-29, "CME Clearing Hurricane Sandy
    # Update": equity index markets reopen today at 5:00 p.m. CT and close tomorrow
    # morning October 30 at 8:15 a.m. CT, via Wayback:
    # https://web.archive.org/web/20260610093358/https://www.cmegroup.com/tools-information/lookups/advisories/clearing/files/Chadv12-464.pdf
    late_open_day(
        date(2012, 10, 30),
        "Hurricane Sandy",
        time(17, 0),
        close=time(8, 15),
    ),
    # source: CME Group press release, 2012-10-30, "CME Group to Reopen U.S. Equity
    # Index Complex Tonight, Resume Normal Trading Hours": equity index markets reopen
    # today at 5:00 p.m. CT, normal hours Wednesday, October 31 (not on Wayback; PR
    # Newswire copy):
    # https://prnewswire.com/news-releases/cme-group-to-reopen-us-equity-index-complex-tonight-resume-normal-trading-hours-176471061.html
    late_open_day(date(2012, 10, 31), "Day after Hurricane Sandy", time(17, 0)),
    # source: CME Globex holiday schedule 2012-thanksgiving-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 11, 22), "Thanksgiving Day", time(10, 30)),
    # source: CME Globex holiday schedule 2012-thanksgiving-done.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 11, 23), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday schedule 2012-christmas .doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    early_close_day(date(2012, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME Globex holiday schedule 2012-christmas .doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    closed_day(date(2012, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2012-christmas .doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    late_open_day(date(2012, 12, 26), "Day after Christmas Day", time(5, 0)),
    # source: CME Globex holiday schedule 2013-new-years.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    closed_day(date(2013, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2013-new-years.doc (in the year's
    # holiday-calendars.zip), CME Group Equity Products row, via Wayback:
    # https://web.archive.org/web/20130309115256/http://www.cmegroup.com/tools-information/holiday-calendar/files/2012-holiday-calendars.zip
    late_open_day(date(2013, 1, 2), "Day after New Year's Day", time(5, 0)),
    # source: CME Globex holiday schedule 2013-martin-luther-king.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 1, 21), "Martin Luther King Jr. Day", time(10, 30)),
    # source: CME Globex holiday schedule 2013-presidents-day.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 2, 18), "Presidents Day", time(10, 30)),
    # source: CME Globex holiday schedule 2013-good-friday.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    closed_day(date(2013, 3, 29), "Good Friday"),
    # source: CME Globex holiday schedule 2013-memorial-day.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 5, 27), "Memorial Day", time(10, 30)),
    # source: CME Globex holiday schedule 2013-4th-of-july-done.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME Globex holiday schedule 2013-4th-of-july-done.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 7, 4), "Independence Day", time(10, 30)),
    # source: CME Globex holiday schedule 2013-labor-day.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 9, 2), "Labor Day", time(10, 30)),
    # source: CME Globex holiday schedule 2013-thanksgiving.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 11, 28), "Thanksgiving Day", time(10, 30)),
    # source: CME Globex holiday schedule 2013-thanksgiving.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 11, 29), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday schedule 2013-christmas.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    early_close_day(date(2013, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME Globex holiday schedule 2013-christmas.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    closed_day(date(2013, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2013-christmas.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    late_open_day(date(2013, 12, 26), "Day after Christmas Day", time(5, 0)),
    # source: CME Globex holiday schedule 2014-new-years.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    closed_day(date(2014, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2014-new-years.pdf (in the year's
    # holiday-calendars.zip), CME & CBOT Equity Products row, via Wayback:
    # https://web.archive.org/web/20140326193052/http://www.cmegroup.com/tools-information/holiday-calendar/files/2013-holiday-calendars.zip
    late_open_day(date(2014, 1, 2), "Day after New Year's Day", time(5, 0)),
    # source: CME Globex holiday schedule 2014-martin-luther-king-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 1, 20), "Martin Luther King Jr. Day", time(10, 30)),
    # source: CME Globex holiday schedule 2014-presidents-day-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 2, 17), "Presidents Day", time(10, 30)),
    # source: CME Globex holiday schedule 2014-good-friday-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    closed_day(date(2014, 4, 18), "Good Friday"),
    # source: CME Globex holiday schedule 2014-memorial-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 5, 26), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2014-4th-of-july-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME Globex holiday schedule 2014-4th-of-july-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2014-labor-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 9, 1), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2014-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 11, 27), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2014-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 11, 28), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday schedule 2014-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    early_close_day(date(2014, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME Globex holiday schedule 2014-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    closed_day(date(2014, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2015-new-years-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20150326121739/http://www.cmegroup.com/tools-information/holiday-calendar/files/2014-holiday-calendars.zip
    closed_day(date(2015, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2015-martin-luther-king-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 1, 19), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-presidents-day-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 2, 16), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-good-friday-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 4, 3), "Good Friday (jobs report)", time(8, 15)),
    # source: CME Globex holiday schedule 2015-memorial-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 5, 25), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-4th-of-july-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 7, 3), "Independence Day (observed)", time(12, 0)),
    # source: CME Globex holiday schedule 2015-labor-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 9, 7), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 11, 26), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2015-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 11, 27), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday schedule 2015-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    early_close_day(date(2015, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME Globex holiday schedule 2015-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    closed_day(date(2015, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2016-new-years-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20160808150227/http://www.cmegroup.com/tools-information/holiday-calendar/files/2015-holiday-calendars.zip
    closed_day(date(2016, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2016-martin-luther-king-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 1, 18), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-presidents-day-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 2, 15), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-good-friday-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    closed_day(date(2016, 3, 25), "Good Friday"),
    # source: CME Globex holiday schedule 2016-memorial-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 5, 30), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-4th-of-july-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-labor-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 9, 5), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 11, 24), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2016-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    early_close_day(date(2016, 11, 25), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday schedule 2016-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    closed_day(date(2016, 12, 26), "Christmas Day (observed)"),
    # source: CME Globex holiday schedule 2017-new-years-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20170628115819/http://www.cmegroup.com/tools-information/holiday-calendar/files/2016-holiday-calendars.zip
    closed_day(date(2017, 1, 2), "New Year's Day (observed)"),
    # source: CME Globex holiday schedule 2017-martin-luther-king-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 1, 16), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-presidents-day-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 2, 20), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-good-friday-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    closed_day(date(2017, 4, 14), "Good Friday"),
    # source: CME Globex holiday schedule 2017-memorial-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 5, 29), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-4th-of-july-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME Globex holiday schedule 2017-4th-of-july-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-labor-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 9, 4), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 11, 23), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2017-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    early_close_day(date(2017, 11, 24), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday schedule 2017-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    closed_day(date(2017, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2018-new-years-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094835/https://www.cmegroup.com/tools-information/holiday-calendar/files/2017-holiday-calendars.zip
    closed_day(date(2018, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2018-martin-luther-king-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 1, 15), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-presidents-day-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 2, 19), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-good-friday-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    closed_day(date(2018, 3, 30), "Good Friday"),
    # source: CME Globex holiday schedule 2018-memorial-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 5, 28), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-4th-of-july-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME Globex holiday schedule 2018-4th-of-july-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-labor-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 9, 3), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 11, 22), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2018-thanksgiving-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 11, 23), "Day after Thanksgiving", time(12, 15)),
    # source: CME Special Executive Report S-8289, 2018-12-03, "National Day of Mourning
    # for Former President George H. W. Bush": U.S.-based equity products close after
    # overnight trading at 8:30 a.m. CT on December 5 and reopen at 5:00 p.m. CT, via
    # Wayback:
    # https://web.archive.org/web/20250103174105/https://www.cmegroup.com/notices/ser/2018/12/SER-8289.pdf
    early_close_day(
        date(2018, 12, 5),
        "National Day of Mourning (George H. W. Bush)",
        time(8, 30),
    ),
    # source: CME Globex holiday schedule 2018-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    early_close_day(date(2018, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME Globex holiday schedule 2018-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    closed_day(date(2018, 12, 25), "Christmas Day"),
    # source: CME Globex holiday schedule 2019-new-years-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100225/https://www.cmegroup.com/tools-information/holiday-calendar/files/2018-holiday-calendars.zip
    closed_day(date(2019, 1, 1), "New Year's Day"),
    # source: CME Globex holiday schedule 2019-mlk-day-schedule.xls (in the year's
    # holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 1, 21), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-presidents-day-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 2, 18), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-good-friday-schedule.xls (in the year's
    # holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    closed_day(date(2019, 4, 19), "Good Friday"),
    # source: CME Globex holiday schedule 2019-memorial-day-schedule.xls (in the year's
    # holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 5, 27), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-independence-day-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME Globex holiday schedule 2019-independence-day-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-labor-day-schedule.xls (in the year's
    # holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 9, 2), "Labor Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-thanksgiving-schedule.xls (in the year's
    # holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 11, 28), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday schedule 2019-thanksgiving-schedule.xls (in the year's
    # holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 11, 29), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday schedule 2019-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    early_close_day(date(2019, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME Globex holiday schedule 2019-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    closed_day(date(2019, 12, 25), "Christmas Day"),
)
