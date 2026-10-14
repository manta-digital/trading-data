"""The CME Globex metals calendar (``CME_METALS``, for GC) — slice 232 TD1, TD2.

An explicit, dated table in the shape of ``CME_EQUITY`` (221 D5): every weekday
from ``CME_METALS_SEED_START`` through ``CME_METALS_HOLIDAYS_SEEDED_THROUGH`` on
which GC did not trade the regular 17:00 (previous day) → 16:00 America/Chicago
session. Every row carries the CME document it was read from; nothing is
generated from a holiday rule or copied from the equity table. Sessions are
dated by the day they close, so a holiday halt is an ``early_close`` on the
holiday itself (221 D4).

Rows were read from the metals row of each CME document: the product-group row
("Energy, Metals, Softs & DME Products" and its variants) of the yearly Globex
holiday spreadsheets for 2020-2022, the METALS row of CME's holiday PDFs for
2023, and product 437 (Gold Futures) of the trading-hours JSON from 2023-09.
Every document read shows the regular 16:00 CT close and 17:00 CT open, so the
TD1 hours hold for the whole range. Every row dated up to 2026-10-13 was also
checked against Databento GLBX.MDP3 GC.FUT trade counts (trades before the
close, none after it; none on a closed day).

Metals differ from equity, and differ between years: US holidays halt at 12:00
through 2021 and 13:30 from 2022; a Friday holiday closes at 12:00 (no reopen
until Sunday); the day after Thanksgiving closes 12:45 through 2023 and 13:45
from 2024; Christmas Eve closes 12:45; Good Friday is closed in the years
equities trade an abbreviated session; the day before Independence Day and the
2025-01-09 National Day of Mourning are regular metals sessions.

The bound is the last year CME has published in full, as for equity. Seed the
next year's rows here, and move the bound, when CME publishes them.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, Final

from manta_trading.market.schema.seed_cme_calendar import (
    CmeCalendarSeed,
    closed_day,
    early_close_day,
)

CME_METALS_CALENDAR_ID: Final = "CME_METALS"

CME_METALS_CALENDAR: Final[dict[str, Any]] = {
    "calendar_id": CME_METALS_CALENDAR_ID,
    "exchange_name": "CME Globex Metals",
    "timezone": "America/Chicago",
    "market_open": time(17, 0),
    "market_close": time(16, 0),
    "extended_open": None,
    "extended_close": None,
    "has_extended_hours": False,
}

CME_METALS_SEED_START: Final = date(2020, 1, 1)
CME_METALS_HOLIDAYS_SEEDED_THROUGH: Final = date(2027, 12, 31)


# Wayback snapshots: the live cmegroup.com refuses automated reads. In the
# trading-hours JSON a holiday "preopen" before 16:00 is the halt, and a day
# with no events means no trading (the same reading as CME_EQUITY's rows).
CME_METALS_EXCEPTIONS: Final[tuple[dict[str, Any], ...]] = (
    # source: CME Globex holiday spreadsheet 2019-2020-new-years-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row,
    # via Wayback:
    # https://web.archive.org/web/20210126094837/https://www.cmegroup.com/tools-information/holiday-calendar/files/2019-holiday-calendars.zip
    closed_day(date(2020, 1, 1), "New Year's Day"),
    # source: CME Globex holiday spreadsheet 2020-mlk-day-schedule.xls (in the year's
    # holiday-calendars.zip), "Energy, Metal, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    early_close_day(date(2020, 1, 20), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-presidents-day-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    early_close_day(date(2020, 2, 17), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-good-friday-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    closed_day(date(2020, 4, 10), "Good Friday"),
    # source: CME Globex holiday spreadsheet 2020-memorial-day-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    early_close_day(date(2020, 5, 25), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-independence-day-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    early_close_day(date(2020, 7, 3), "Independence Day (observed)", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-labor-day-schedule.xls (in the year's
    # holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    early_close_day(date(2020, 9, 7), "Labor Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-thanksgiving-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    early_close_day(date(2020, 11, 26), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2020-thanksgiving-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    early_close_day(date(2020, 11, 27), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday spreadsheet 2020-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    early_close_day(date(2020, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME Globex holiday spreadsheet 2020-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    closed_day(date(2020, 12, 25), "Christmas Day"),
    # source: CME Globex holiday spreadsheet 2021-new-years-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260730111834/https://www.cmegroup.com/tools-information/holiday-calendar/files/2020-holiday-calendars.zip
    closed_day(date(2021, 1, 1), "New Year's Day"),
    # source: CME Globex holiday spreadsheet 2021-mlk-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metal, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    early_close_day(date(2021, 1, 18), "Martin Luther King Jr. Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-presidents-day-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row,
    # via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    early_close_day(date(2021, 2, 15), "Presidents Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-good-friday-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    closed_day(date(2021, 4, 2), "Good Friday"),
    # source: CME Globex holiday spreadsheet 2021-memorial-day-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    early_close_day(date(2021, 5, 31), "Memorial Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-independence-day-holiday-schedule.xls
    # (in the year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row,
    # via Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    early_close_day(date(2021, 7, 5), "Independence Day (observed)", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-labor-day-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    early_close_day(date(2021, 9, 6), "Labor Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-thanksgiving-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    early_close_day(date(2021, 11, 25), "Thanksgiving Day", time(12, 0)),
    # source: CME Globex holiday spreadsheet 2021-thanksgiving-holiday-schedule.xls (in
    # the year's holiday-calendars.zip), "Energy, Metals, Softs & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    early_close_day(date(2021, 11, 26), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday spreadsheet 2021-christmas-holiday-schedule.xls (in the
    # year's holiday-calendars.zip), "Energy, Metals, Softs & DME products" row, via
    # Wayback:
    # https://web.archive.org/web/20260830100327/https://www.cmegroup.com/tools-information/holiday-calendar/files/2021-holiday-calendars.zip
    closed_day(date(2021, 12, 24), "Christmas Day (observed)"),
    # source: CME Globex holiday spreadsheet, "Energy, Metals. Softs & DME Products"
    # row, via Wayback:
    # https://web.archive.org/web/20220117212230/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-mlk-day-holiday-schedule.xls
    early_close_day(date(2022, 1, 17), "Martin Luther King Jr. Day", time(13, 30)),
    # source: CME Globex holiday spreadsheet, "Energy, Metals, Softs & DME Products"
    # row, via Wayback:
    # https://web.archive.org/web/20220704073810/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-presidents-day-holiday-schedule.xls
    early_close_day(date(2022, 2, 21), "Presidents Day", time(13, 30)),
    # source: CME Globex holiday spreadsheet, "Energy, Metals, Softs & DME Products"
    # row, via Wayback:
    # https://web.archive.org/web/20220704065501/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-good-friday-holiday-schedule.xls
    closed_day(date(2022, 4, 15), "Good Friday"),
    # source: CME Globex holiday spreadsheet, "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20220704065438/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-memorial-day-holiday-schedule.xls
    early_close_day(date(2022, 5, 30), "Memorial Day", time(13, 30)),
    # source: CME Globex holiday spreadsheet, "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20220620200210/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-juneteenth-holiday-schedule.xls
    early_close_day(date(2022, 6, 20), "Juneteenth (observed)", time(13, 30)),
    # source: CME Globex holiday spreadsheet, "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20220704065450/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-independence-day-holiday-schedule.xls
    early_close_day(date(2022, 7, 4), "Independence Day", time(13, 30)),
    # source: CME Globex holiday spreadsheet, "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20220704065441/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-labor-day-holiday-schedule.xls
    early_close_day(date(2022, 9, 5), "Labor Day", time(13, 30)),
    # source: CME Globex holiday spreadsheet, "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20221125112802/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-thanksgiving-holiday-schedule.xls
    early_close_day(date(2022, 11, 24), "Thanksgiving Day", time(13, 30)),
    # source: CME Globex holiday spreadsheet, "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20221125112802/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-thanksgiving-holiday-schedule.xls
    early_close_day(date(2022, 11, 25), "Day after Thanksgiving", time(12, 45)),
    # source: CME Globex holiday spreadsheet, "Energy, Metals & DME products" row, via
    # Wayback:
    # https://web.archive.org/web/20220704065430/https://www.cmegroup.com/tools-information/holiday-calendar/files/2022-christmas-holiday-schedule.xls
    closed_day(date(2022, 12, 26), "Christmas Day (observed)"),
    # source: CME Globex holiday spreadsheet, "Energy, Metals & DME Products" row, via
    # Wayback:
    # https://web.archive.org/web/20220704065501/https://www.cmegroup.com/tools-information/holiday-calendar/files/2023-new-years-holiday-schedule.xls
    closed_day(date(2023, 1, 2), "New Year's Day (observed)"),
    # source: no CME document archived for this date. Databento GLBX.MDP3 GC.FUT trades,
    # free metadata.get_record_count (2026-10-14): 222 trades 13:00-13:30 CT, 0 trades
    # 13:30-16:59 CT. Matches CME's 13:30 metals halt on every other 2022-2023 US
    # holiday (same method as CME_EQUITY's row).
    early_close_day(date(2023, 1, 16), "Martin Luther King Jr. Day", time(13, 30)),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20230329115747/https://www.cmegroup.com/files/presidents-day.pdf
    early_close_day(date(2023, 2, 20), "Presidents Day", time(13, 30)),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20240708160009/https://www.cmegroup.com/files/good-friday.pdf
    closed_day(date(2023, 4, 7), "Good Friday"),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20230420224018/https://www.cmegroup.com/trading-hours/files/memorial-day-2023.pdf
    early_close_day(date(2023, 5, 29), "Memorial Day", time(13, 30)),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20230613185949/https://www.cmegroup.com/trading-hours/files/juneteenth-2023.pdf
    early_close_day(date(2023, 6, 19), "Juneteenth", time(13, 30)),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20230627125057/https://www.cmegroup.com/trading-hours/files/4th-of-july-2023.pdf
    early_close_day(date(2023, 7, 4), "Independence Day", time(13, 30)),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20230802192446/https://www.cmegroup.com/trading-hours/files/labor-day-2023.pdf
    early_close_day(date(2023, 9, 4), "Labor Day", time(13, 30)),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20231203205929/https://www.cmegroup.com/trading-hours/files/thanksgiving-day-2023.pdf
    early_close_day(date(2023, 11, 23), "Thanksgiving Day", time(13, 30)),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20231203205929/https://www.cmegroup.com/trading-hours/files/thanksgiving-day-2023.pdf
    early_close_day(date(2023, 11, 24), "Day after Thanksgiving", time(12, 45)),
    # source: CME holiday schedule PDF, METALS row, via Wayback:
    # https://web.archive.org/web/20260719095248/https://www.cmegroup.com/trading-hours/files/christmas-day-2023.pdf
    closed_day(date(2023, 12, 25), "Christmas Day"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2023-12-31&toEventDate=2024-01-02&isProtected&_t=1734710019525
    closed_day(date(2024, 1, 1), "New Year's Day"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-01-14&toEventDate=2024-01-16&isProtected&_t=1734710019526
    early_close_day(date(2024, 1, 15), "Martin Luther King Jr. Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-02-18&toEventDate=2024-02-20&isProtected&_t=1734710019528
    early_close_day(date(2024, 2, 19), "Presidents Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-03-28&toEventDate=2024-03-30&isProtected&_t=1734710019530
    closed_day(date(2024, 3, 29), "Good Friday"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155339/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-05-26&toEventDate=2024-05-28&isProtected&_t=1734710019531
    early_close_day(date(2024, 5, 27), "Memorial Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-06-18&toEventDate=2024-06-20&isProtected&_t=1734710019532
    early_close_day(date(2024, 6, 19), "Juneteenth", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-07-03&toEventDate=2024-07-05&isProtected&_t=1734710019533
    early_close_day(date(2024, 7, 4), "Independence Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-09-01&toEventDate=2024-09-03&isProtected&_t=1734710019534
    early_close_day(date(2024, 9, 2), "Labor Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-11-27&toEventDate=2024-11-29&isProtected&_t=1734710019535
    early_close_day(date(2024, 11, 28), "Thanksgiving Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-11-27&toEventDate=2024-11-29&isProtected&_t=1734710019535
    early_close_day(date(2024, 11, 29), "Day after Thanksgiving", time(13, 45)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-12-24&toEventDate=2024-12-26&isProtected&_t=1734710019537
    early_close_day(date(2024, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-12-24&toEventDate=2024-12-26&isProtected&_t=1734710019537
    closed_day(date(2024, 12, 25), "Christmas Day"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2024-12-31&toEventDate=2025-01-02&isProtected&_t=1734710019538
    closed_day(date(2025, 1, 1), "New Year's Day"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-01-19&toEventDate=2025-01-21&isProtected&_t=1734710019539
    early_close_day(date(2025, 1, 20), "Martin Luther King Jr. Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-02-16&toEventDate=2025-02-18&isProtected&_t=1734710019540
    early_close_day(date(2025, 2, 17), "Presidents Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-04-17&toEventDate=2025-04-19&isProtected&_t=1734710019542
    closed_day(date(2025, 4, 18), "Good Friday"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-05-25&toEventDate=2025-05-27&isProtected&_t=1734710019543
    early_close_day(date(2025, 5, 26), "Memorial Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-06-18&toEventDate=2025-06-20&isProtected&_t=1734710019544
    early_close_day(date(2025, 6, 19), "Juneteenth", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-07-03&toEventDate=2025-07-05&isProtected&_t=1734710019545
    early_close_day(date(2025, 7, 4), "Independence Day", time(12, 0)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20241220155340/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-08-31&toEventDate=2025-09-02&isProtected&_t=1734710019546
    early_close_day(date(2025, 9, 1), "Labor Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260129012309/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-11-26&toEventDate=2025-11-28&isProtected&_t=1769649789739
    early_close_day(date(2025, 11, 27), "Thanksgiving Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260129012309/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-11-26&toEventDate=2025-11-28&isProtected&_t=1769649789739
    early_close_day(date(2025, 11, 28), "Day after Thanksgiving", time(13, 45)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260129012309/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-12-24&toEventDate=2025-12-26&isProtected&_t=1769649789742
    early_close_day(date(2025, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260129012309/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-12-24&toEventDate=2025-12-26&isProtected&_t=1769649789742
    closed_day(date(2025, 12, 25), "Christmas Day"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260722114222/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2025-12-31&toEventDate=2026-01-02&isProtected&_t=1784720542553
    closed_day(date(2026, 1, 1), "New Year's Day"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260812185118/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-01-18&toEventDate=2026-01-20&isProtected&_t=1720455320274
    early_close_day(date(2026, 1, 19), "Martin Luther King Jr. Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260812185118/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-02-15&toEventDate=2026-02-17&isProtected&_t=1720455320274
    early_close_day(date(2026, 2, 16), "Presidents Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260722114222/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-04-01&toEventDate=2026-04-03&isProtected&_t=1784720542559
    closed_day(date(2026, 4, 3), "Good Friday (jobs report)"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260722114222/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-05-24&toEventDate=2026-05-26&isProtected&_t=1784720542560
    early_close_day(date(2026, 5, 25), "Memorial Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260722114222/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-06-18&toEventDate=2026-06-20&isProtected&_t=1784720542562
    early_close_day(date(2026, 6, 19), "Juneteenth", time(12, 0)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20260722114223/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-07-03&toEventDate=2026-07-05&isProtected&_t=1784720542563
    early_close_day(date(2026, 7, 3), "Independence Day (observed)", time(12, 0)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060748/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-09-06&toEventDate=2026-09-08&isProtected&_t=1791266868928
    early_close_day(date(2026, 9, 7), "Labor Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060750/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-11-25&toEventDate=2026-11-27&isProtected&_t=1791266870317
    early_close_day(date(2026, 11, 26), "Thanksgiving Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060750/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-11-25&toEventDate=2026-11-27&isProtected&_t=1791266870317
    early_close_day(date(2026, 11, 27), "Day after Thanksgiving", time(13, 45)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-12-24&toEventDate=2026-12-26&isProtected&_t=1791266868936
    early_close_day(date(2026, 12, 24), "Christmas Eve", time(12, 45)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-12-24&toEventDate=2026-12-26&isProtected&_t=1791266868936
    closed_day(date(2026, 12, 25), "Christmas Day"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2026-12-31&toEventDate=2027-01-02&isProtected&_t=1791266868938
    closed_day(date(2027, 1, 1), "New Year's Day"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-01-17&toEventDate=2027-01-19&isProtected&_t=1791266868948
    early_close_day(date(2027, 1, 18), "Martin Luther King Jr. Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-02-14&toEventDate=2027-02-16&isProtected&_t=1791266868949
    early_close_day(date(2027, 2, 15), "Presidents Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-03-25&toEventDate=2027-03-27&isProtected&_t=1791266868951
    closed_day(date(2027, 3, 26), "Good Friday"),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-05-30&toEventDate=2027-06-01&isProtected&_t=1791266868952
    early_close_day(date(2027, 5, 31), "Memorial Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-06-17&toEventDate=2027-06-19&isProtected&_t=1791266868953
    early_close_day(date(2027, 6, 18), "Juneteenth (observed)", time(12, 0)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-07-04&toEventDate=2027-07-06&isProtected&_t=1791266868956
    early_close_day(date(2027, 7, 5), "Independence Day (observed)", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-09-05&toEventDate=2027-09-07&isProtected&_t=1791266868957
    early_close_day(date(2027, 9, 6), "Labor Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-11-24&toEventDate=2027-11-26&isProtected&_t=1791266868958
    early_close_day(date(2027, 11, 25), "Thanksgiving Day", time(13, 30)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-11-24&toEventDate=2027-11-26&isProtected&_t=1791266868958
    early_close_day(date(2027, 11, 26), "Day after Thanksgiving", time(13, 45)),
    # source: CME trading-hours JSON, product 437 (Gold Futures), via Wayback:
    # https://web.archive.org/web/20261006060749/https://www.cmegroup.com/services/trading-hours-by-product?id=316,133,425,300,58,437,22,8478,5201,10191&pageNumber=1&pageSize=999&sortAsc=true&fromEventDate=2027-12-22&toEventDate=2027-12-24&isProtected&_t=1791266868960
    closed_day(date(2027, 12, 24), "Christmas Day (observed)"),
)

CME_METALS_SEED: Final = CmeCalendarSeed(
    calendar_id=CME_METALS_CALENDAR_ID,
    calendar=CME_METALS_CALENDAR,
    seed_start=CME_METALS_SEED_START,
    holidays_seeded_through=CME_METALS_HOLIDAYS_SEEDED_THROUGH,
    exceptions=CME_METALS_EXCEPTIONS,
)
