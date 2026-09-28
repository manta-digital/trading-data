"""The CME Globex equity calendar (``CME_EQUITY``, for ES) — slice 221 D2, D5.

An explicit, dated table: every weekday from ``CME_EQUITY_SEED_START`` through
``CME_EQUITY_HOLIDAYS_SEEDED_THROUGH`` on which ES did not trade the regular
17:00 (previous day) → 16:00 America/Chicago session. Every row carries the
CME document it was read from; nothing is generated from a holiday rule.
Sessions are dated by the day they close, so a US-holiday halt at 12:00 CT is
an ``early_close`` on the holiday itself (D4).

Regular hours (D2) were confirmed constant from 2020-01-01 on: ES contract
specs (Wayback 2019-12-21, 2020-06-01) read "Sunday - Friday 6:00 p.m. -
5:00 p.m. ET", and every CME holiday schedule 2020-2027 shows a 16:00 CT close
and 17:00 CT open around its holiday. The 2021 removal of the 15:15-15:30 CT
halt (SER-8776) is inside a session and does not change it.

The bound is the last year CME has published in full: the trading-hours page
(Wayback 2026-09-04) carries every 2027 holiday. Seed the next year's schedule
here, and move the bound, when CME publishes it; ``mt data extend`` says when
the bound is what stops the horizon (D6).

``generate_calendar_insert_sql`` in ``seed_calendar`` is deliberately not used:
migrations 007/008 render through it, and it predates ``holidays_seeded_through``.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, Final

from manta_trading.data.base.trading_calendar import MarketStatus

CME_EQUITY_CALENDAR_ID: Final = "CME_EQUITY"

CME_EQUITY_CALENDAR: Final[dict[str, Any]] = {
    "calendar_id": CME_EQUITY_CALENDAR_ID,
    "exchange_name": "CME Globex Equity Index",
    "timezone": "America/Chicago",
    "market_open": time(17, 0),
    "market_close": time(16, 0),
    "extended_open": None,
    "extended_close": None,
    "has_extended_hours": False,
}

CME_EQUITY_SEED_START: Final = date(2020, 1, 1)
CME_EQUITY_HOLIDAYS_SEEDED_THROUGH: Final = date(2027, 12, 31)


def _closed(day: date, name: str) -> dict[str, Any]:
    return {
        "holiday_date": day,
        "holiday_name": name,
        "market_status": MarketStatus.CLOSED,
        "early_close_time": None,
        "late_open_time": None,
    }


def _early_close(day: date, name: str, close: time) -> dict[str, Any]:
    return {
        "holiday_date": day,
        "holiday_name": name,
        "market_status": MarketStatus.EARLY_CLOSE,
        "early_close_time": close,
        "late_open_time": None,
    }


# Wayback snapshots: the live cmegroup.com refuses automated reads. The JSON
# rows are the service behind cmegroup.com/trading-hours.html; there a 12:00
# "preopen" on a holiday is the halt, and a day with no events means no trading.
CME_EQUITY_EXCEPTIONS: Final[tuple[dict[str, Any], ...]] = (
    # source: CME Globex holiday spreadsheet 2019-2020-new-years-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    _closed(date(2020, 1, 1), "New Year's Day"),
    # source: CME Globex holiday spreadsheet 2020-mlk-day-schedule.xls (in the year's
    # holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _early_close(date(2020, 1, 20), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-presidents-day-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _early_close(date(2020, 2, 17), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-good-friday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _closed(date(2020, 4, 10), "Good Friday"),
    # source: CME Globex holiday spreadsheet 2020-memorial-day-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _early_close(date(2020, 5, 25), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-independence-day-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _early_close(date(2020, 7, 3), "Independence Day (observed)", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-labor-day-schedule.xls (in the year's
    # holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _early_close(date(2020, 9, 7), "Labor Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-thanksgiving-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _early_close(date(2020, 11, 26), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-thanksgiving-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _early_close(date(2020, 11, 27), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday spreadsheet 2020-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _early_close(date(2020, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME Globex holiday spreadsheet 2020-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _closed(date(2020, 12, 25), "Christmas Day"),
    # source: CME Globex holiday spreadsheet 2021-new-years-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    _closed(date(2021, 1, 1), "New Year's Day"),
    # source: CME Globex holiday spreadsheet 2021-mlk-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _early_close(date(2021, 1, 18), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-presidents-day-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _early_close(date(2021, 2, 15), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-good-friday-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _early_close(date(2021, 4, 2), "Good Friday (jobs report)", time(8, 15)),
    # source: CME Globex holiday spreadsheet 2021-memorial-day-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _early_close(date(2021, 5, 31), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-independence-day-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _early_close(date(2021, 7, 5), "Independence Day (observed)", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-labor-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _early_close(date(2021, 9, 6), "Labor Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-thanksgiving-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _early_close(date(2021, 11, 25), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-thanksgiving-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _early_close(date(2021, 11, 26), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday spreadsheet 2021-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), Equity Products row, via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    _closed(date(2021, 12, 24), "Christmas Day (observed)"),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20220117212230/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-mlk-day-holiday-schedule.xls
    _early_close(date(2022, 1, 17), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20220704073810/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-presidents-day-holiday-schedule.xls
    _early_close(date(2022, 2, 21), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20220704065501/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-good-friday-holiday-schedule.xls
    _closed(date(2022, 4, 15), "Good Friday"),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20220704065438/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-memorial-day-holiday-schedule.xls
    _early_close(date(2022, 5, 30), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20220620200210/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-juneteenth-holiday-schedule.xls
    _early_close(date(2022, 6, 20), "Juneteenth (observed)", time(12, 0)),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20220704065450/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-independence-day-holiday-schedule.xls
    _early_close(date(2022, 7, 4), "Independence Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20220704065441/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-labor-day-holiday-schedule.xls
    _early_close(date(2022, 9, 5), "Labor Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20221125112802/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-thanksgiving-holiday-schedule.xls
    _early_close(date(2022, 11, 24), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet, Equity Products row, via Wayback:
    # https://web.archive.org/web/20221125112802/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-thanksgiving-holiday-schedule.xls
    _early_close(date(2022, 11, 25), "Day after Thanksgiving", time(12, 15)),
    # source: CME Globex holiday spreadsheet, Equity Products row; preliminary sheet
    # posted six months ahead; no final version archived, via Wayback:
    # https://web.archive.org/web/20220704065430/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-christmas-holiday-schedule.xls
    _closed(date(2022, 12, 26), "Christmas Day (observed)"),
    # source: CME Globex holiday spreadsheet, Equity Products row; preliminary sheet
    # posted six months ahead; no final version archived, via Wayback:
    # https://web.archive.org/web/20220704065501/https://www.cmegroup.com/tools-information/holiday-calendar/files/2023-new-years-holiday-schedule.xls
    _closed(date(2023, 1, 2), "New Year's Day (observed)"),
    # source: no CME document archived for this date. Databento GLBX.MDP3
    # ES.FUT trades, free metadata.get_record_count (2026-09-28): 486 trades
    # 11:55-12:00 CT, 0 trades 12:00-16:59 CT; 15,096 trades 08:00-11:59 CT;
    # 93,388 trades 12:01-15:59 CT on 2023-01-17. Matches CME's 12:00 halt on
    # every other US holiday (PM decision, 2026-09-28).
    _early_close(date(2023, 1, 16), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME holiday schedule PDF, EQUITIES row, via Wayback:
    # https://web.archive.org/web/20230329115747/https://www.cmegroup.com/files/presidents-day.pdf
    _early_close(date(2023, 2, 20), "Presidents Day", time(12, 0)),
    # source: CME holiday schedule PDF, EQUITIES row, via Wayback:
    # https://web.archive.org/web/20240708160009/https://www.cmegroup.com/files/good-friday.pdf
    _early_close(date(2023, 4, 7), "Good Friday (jobs report)", time(8, 15)),
    # source: CME holiday schedule PDF, EQUITIES row, via Wayback:
    # https://web.archive.org/web/20230420224018/https://www.cmegroup.com/trading-hours/files/memorial-day-2023.pdf
    _early_close(date(2023, 5, 29), "Memorial Day", time(12, 0)),
    # source: CME holiday schedule PDF, EQUITIES row, via Wayback:
    # https://web.archive.org/web/20230613185949/https://www.cmegroup.com/trading-hours/files/juneteenth-2023.pdf
    _early_close(date(2023, 6, 19), "Juneteenth", time(12, 0)),
    # source: CME holiday schedule PDF, EQUITIES row, via Wayback:
    # https://web.archive.org/web/20230627125057/https://www.cmegroup.com/trading-hours/files/4th-of-july-2023.pdf
    _early_close(date(2023, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME holiday schedule PDF, EQUITIES row, via Wayback:
    # https://web.archive.org/web/20230627125057/https://www.cmegroup.com/trading-hours/files/4th-of-july-2023.pdf
    _early_close(date(2023, 7, 4), "Independence Day", time(12, 0)),
    # source: CME holiday schedule PDF, EQUITIES row; CME trading-hours JSON, product
    # 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20230802192446/https://www.cmegroup.com/trading-hours/files/labor-day-2023.pdf
    # https://web.archive.org/web/20240708161439/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2023-09-03&toEventDate=2023-09-05&isProtected&_t=1720455278654
    _early_close(date(2023, 9, 4), "Labor Day", time(12, 0)),
    # source: CME holiday schedule PDF, EQUITIES row, via Wayback:
    # https://web.archive.org/web/20231203205929/https://www.cmegroup.com/trading-hours/files/thanksgiving-day-2023.pdf
    _early_close(date(2023, 11, 23), "Thanksgiving Day", time(12, 0)),
    # source: CME holiday schedule PDF, EQUITIES row, via Wayback:
    # https://web.archive.org/web/20231203205929/https://www.cmegroup.com/trading-hours/files/thanksgiving-day-2023.pdf
    _early_close(date(2023, 11, 24), "Day after Thanksgiving", time(12, 15)),
    # source: CME holiday schedule PDF, EQUITIES row; CME trading-hours JSON, product
    # 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260719095248/https://www.cmegroup.com/trading-hours/files/christmas-day-2023.pdf
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2023-12-24&toEventDate=2023-12-26&isProtected&_t=1734710019523
    _closed(date(2023, 12, 25), "Christmas Day"),
    # source: CME holiday schedule PDF, EQUITIES row; CME trading-hours JSON, product
    # 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260811165716/https://www.cmegroup.com/trading-hours/files/new-years-day-2024.pdf
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2023-12-31&toEventDate=2024-01-02&isProtected&_t=1734710019525
    _closed(date(2024, 1, 1), "New Year's Day"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-01-14&toEventDate=2024-01-16&isProtected&_t=1734710019526
    _early_close(date(2024, 1, 15), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-02-18&toEventDate=2024-02-20&isProtected&_t=1734710019528
    _early_close(date(2024, 2, 19), "Presidents Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-03-28&toEventDate=2024-03-30&isProtected&_t=1734710019530
    _closed(date(2024, 3, 29), "Good Friday"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-05-26&toEventDate=2024-05-28&isProtected&_t=1734710019531
    _early_close(date(2024, 5, 27), "Memorial Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-06-18&toEventDate=2024-06-20&isProtected&_t=1734710019532
    _early_close(date(2024, 6, 19), "Juneteenth", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-07-03&toEventDate=2024-07-05&isProtected&_t=1734710019533
    _early_close(date(2024, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-07-03&toEventDate=2024-07-05&isProtected&_t=1734710019533
    _early_close(date(2024, 7, 4), "Independence Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-09-01&toEventDate=2024-09-03&isProtected&_t=1734710019534
    _early_close(date(2024, 9, 2), "Labor Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-11-27&toEventDate=2024-11-29&isProtected&_t=1734710019535
    _early_close(date(2024, 11, 28), "Thanksgiving Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-11-27&toEventDate=2024-11-29&isProtected&_t=1734710019535
    _early_close(date(2024, 11, 29), "Day after Thanksgiving", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-12-24&toEventDate=2024-12-26&isProtected&_t=1734710019537
    _early_close(date(2024, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-12-24&toEventDate=2024-12-26&isProtected&_t=1734710019537
    _closed(date(2024, 12, 25), "Christmas Day"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-12-31&toEventDate=2025-01-02&isProtected&_t=1734710019538
    _closed(date(2025, 1, 1), "New Year's Day"),
    # source: CME holiday schedule PDF, EQUITIES row; CME press release, via Wayback:
    # https://web.archive.org/web/20250218194143/https://www.cmegroup.com/trading-hours/files/day-of-mourning-january-9-2024.pdf
    # https://web.archive.org/web/20251116145315/https://www.cmegroup.com/media-room/press-releases/2025/12/30/cme_group_announcestradinghoursforusnationaldayofmourningtohonor.html
    _early_close(
        date(2025, 1, 9), "National Day of Mourning (President Carter)", time(8, 30)
    ),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-01-19&toEventDate=2025-01-21&isProtected&_t=1734710019539
    _early_close(date(2025, 1, 20), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-02-16&toEventDate=2025-02-18&isProtected&_t=1734710019540
    _early_close(date(2025, 2, 17), "Presidents Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-04-17&toEventDate=2025-04-19&isProtected&_t=1734710019542
    _closed(date(2025, 4, 18), "Good Friday"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-05-25&toEventDate=2025-05-27&isProtected&_t=1734710019543
    _early_close(date(2025, 5, 26), "Memorial Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-06-18&toEventDate=2025-06-20&isProtected&_t=1734710019544
    _early_close(date(2025, 6, 19), "Juneteenth", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-07-03&toEventDate=2025-07-05&isProtected&_t=1734710019545
    _early_close(date(2025, 7, 3), "Independence Day eve", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-07-03&toEventDate=2025-07-05&isProtected&_t=1734710019545
    _early_close(date(2025, 7, 4), "Independence Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-08-31&toEventDate=2025-09-02&isProtected&_t=1734710019546
    _early_close(date(2025, 9, 1), "Labor Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260129012309/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-11-26&toEventDate=2025-11-28&isProtected&_t=1769649789739
    _early_close(date(2025, 11, 27), "Thanksgiving Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260129012309/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-11-26&toEventDate=2025-11-28&isProtected&_t=1769649789739
    _early_close(date(2025, 11, 28), "Day after Thanksgiving", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260129012309/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-12-24&toEventDate=2025-12-26&isProtected&_t=1769649789742
    _early_close(date(2025, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260129012309/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-12-24&toEventDate=2025-12-26&isProtected&_t=1769649789742
    _closed(date(2025, 12, 25), "Christmas Day"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260722114222/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-12-31&toEventDate=2026-01-02&isProtected&_t=1784720542553
    _closed(date(2026, 1, 1), "New Year's Day"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260812185118/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-01-18&toEventDate=2026-01-20&isProtected&_t=1720455320274
    _early_close(date(2026, 1, 19), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260812185118/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-02-15&toEventDate=2026-02-17&isProtected&_t=1720455320274
    _early_close(date(2026, 2, 16), "Presidents Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260722114222/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-04-01&toEventDate=2026-04-03&isProtected&_t=1784720542559
    _early_close(date(2026, 4, 3), "Good Friday (jobs report)", time(8, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260722114222/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-05-24&toEventDate=2026-05-26&isProtected&_t=1784720542560
    _early_close(date(2026, 5, 25), "Memorial Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260722114222/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-06-18&toEventDate=2026-06-20&isProtected&_t=1784720542562
    # https://web.archive.org/web/20260129012310/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-06-17&toEventDate=2026-06-19&isProtected&_t=1769649789748
    _early_close(date(2026, 6, 19), "Juneteenth", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260722114223/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-07-03&toEventDate=2026-07-05&isProtected&_t=1784720542563
    # https://web.archive.org/web/20260129012310/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-07-02&toEventDate=2026-07-04&isProtected&_t=1769649789749
    _early_close(date(2026, 7, 3), "Independence Day (observed)", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913150612/https://www.cmegroup.com/services/trading-hours-by-product?id=133,219&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-09-06&toEventDate=2026-09-08&isProtected
    _early_close(date(2026, 9, 7), "Labor Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-11-25&toEventDate=2026-11-27&isProtected&_t=1789285658800
    _early_close(date(2026, 11, 26), "Thanksgiving Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-11-25&toEventDate=2026-11-27&isProtected&_t=1789285658800
    _early_close(date(2026, 11, 27), "Day after Thanksgiving", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260925004747/https://www.cmegroup.com/services/trading-hours-by-product?id=167,133&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-12-24&toEventDate=2026-12-26&isProtected
    # https://web.archive.org/web/20260310063244/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-12-23&toEventDate=2026-12-25&isProtected&_t=1720050009698
    _early_close(date(2026, 12, 24), "Christmas Eve", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260925004747/https://www.cmegroup.com/services/trading-hours-by-product?id=167,133&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-12-24&toEventDate=2026-12-26&isProtected
    _closed(date(2026, 12, 25), "Christmas Day"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074737/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-12-31&toEventDate=2027-01-02&isProtected&_t=1789285657335
    # https://web.archive.org/web/20260310063233/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-12-30&toEventDate=2027-01-01&isProtected&_t=1720050009698
    _closed(date(2027, 1, 1), "New Year's Day"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074737/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-01-17&toEventDate=2027-01-19&isProtected&_t=1789285657337
    _early_close(date(2027, 1, 18), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-02-14&toEventDate=2027-02-16&isProtected&_t=1789285657338
    _early_close(date(2027, 2, 15), "Presidents Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-03-25&toEventDate=2027-03-27&isProtected&_t=1789285657339
    _closed(date(2027, 3, 26), "Good Friday"),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-05-30&toEventDate=2027-06-01&isProtected&_t=1789285657339
    _early_close(date(2027, 5, 31), "Memorial Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-06-17&toEventDate=2027-06-19&isProtected&_t=1789285657340
    _early_close(date(2027, 6, 18), "Juneteenth (observed)", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-07-04&toEventDate=2027-07-06&isProtected&_t=1789285657341
    _early_close(date(2027, 7, 5), "Independence Day (observed)", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-09-05&toEventDate=2027-09-07&isProtected&_t=1789285657342
    _early_close(date(2027, 9, 6), "Labor Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-11-24&toEventDate=2027-11-26&isProtected&_t=1789285657343
    _early_close(date(2027, 11, 25), "Thanksgiving Day", time(12, 0)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-11-24&toEventDate=2027-11-26&isProtected&_t=1789285657343
    _early_close(date(2027, 11, 26), "Day after Thanksgiving", time(12, 15)),
    # source: CME trading-hours JSON, product 133 (E-mini S&P 500), via Wayback:
    # https://web.archive.org/web/20260913074738/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-12-22&toEventDate=2027-12-24&isProtected&_t=1789285657344
    _closed(date(2027, 12, 24), "Christmas Day (observed)"),
)

_CALENDAR_COLUMNS: Final = (
    "calendar_id",
    "exchange_name",
    "timezone",
    "market_open",
    "market_close",
    "extended_open",
    "extended_close",
    "has_extended_hours",
    "holidays_seeded_through",
)
_HOLIDAY_COLUMNS: Final = (
    "calendar_id",
    "holiday_date",
    "holiday_name",
    "market_status",
    "early_close_time",
    "late_open_time",
)


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    names = ", ".join(columns)
    params = ", ".join(f"%({c})s" for c in columns)
    return f"INSERT INTO {table} ({names}) VALUES ({params}) ON CONFLICT DO NOTHING"


def cme_equity_calendar_insert() -> tuple[str, dict[str, Any]]:
    """Parameterized INSERT for the ``CME_EQUITY`` trading_calendars row."""
    params = {
        **CME_EQUITY_CALENDAR,
        "holidays_seeded_through": CME_EQUITY_HOLIDAYS_SEEDED_THROUGH,
    }
    return _insert_sql("trading_calendars", _CALENDAR_COLUMNS), params


def cme_equity_holidays_insert() -> tuple[str, list[dict[str, Any]]]:
    """Parameterized INSERT (for ``executemany``) of every exception row."""
    rows = [
        {
            **row,
            "calendar_id": CME_EQUITY_CALENDAR_ID,
            "market_status": row["market_status"].value,
        }
        for row in CME_EQUITY_EXCEPTIONS
    ]
    return _insert_sql("trading_holidays", _HOLIDAY_COLUMNS), rows
