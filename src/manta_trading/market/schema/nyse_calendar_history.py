"""NYSE and NASDAQ holidays as explicit dated rows, 2020-2028 (slice 174 D8, #26).

Every full closure and early close, typed from NYSE's published schedule; nothing
is generated from a rule. ``seed_calendar.generate_holidays`` stays only for
migrations 007/008, which a fresh database replays. Against it, 2020-2026 differ
on four dates, all confirmed by the sources below and by production minutes:

- 2021-12-31 is open: NYSE observed no New Year's holiday for 2022 (Rule 7.2).
- 2025-01-09 is closed: the National Day of Mourning for President Carter.
- 2020-07-02 and 2026-07-02 are full days: with July 4 on a Saturday, NYSE closes
  on Friday the 3rd and does not shorten Thursday.

Early closes are 13:00 ET; NYSE Arca's late session closes at 17:00 ET on those
days (the footnotes below), so each early close carries a 17:00 extended close.

Sources (NYSE "Holidays & Trading Hours", three years per page; Wayback snapshots
where the year has left the live page):

- 2020: https://web.archive.org/web/20200613190802/https://www.nyse.com/markets/hours-calendars
- 2021, 2022: https://web.archive.org/web/20211213112436/https://www.nyse.com/markets/hours-calendars
- 2023, 2024: https://web.archive.org/web/20221216142429/https://www.nyse.com/markets/hours-calendars
- 2025: https://web.archive.org/web/20230613043731/https://www.nyse.com/markets/hours-calendars
  and https://web.archive.org/web/20250331204034/https://www.nyse.com/markets/hours-calendars
- 2025-01-09: ICE release of 2024-12-30, https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx
- 2026, 2027, 2028: https://www.nyse.com/markets/hours-calendars (read 2026-11-19),
  cross-checked against https://www.nyse.com/publicdocs/Trading_Days.pdf (251 US
  cash-equity trading days in each year).

NASDAQ observes the same days; both calendars take these rows.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, Final

from manta_trading.data.base.trading_calendar import MarketStatus

NYSE_HOLIDAYS_SEEDED_THROUGH: Final = date(2028, 12, 31)
"""The last date the rows below cover; both calendars' ``holidays_seeded_through``."""

US_EQUITY_CALENDAR_IDS: Final = ("NYSE", "NASDAQ")

EARLY_CLOSE: Final = time(13, 0)
EARLY_EXTENDED_CLOSE: Final = time(17, 0)


def _closed(day: date, name: str) -> dict[str, Any]:
    return {
        "holiday_date": day,
        "holiday_name": name,
        "market_status": MarketStatus.CLOSED,
        "early_close_time": None,
        "late_open_time": None,
        "extended_close_time": None,
    }


def _early(day: date, name: str) -> dict[str, Any]:
    return {
        "holiday_date": day,
        "holiday_name": name,
        "market_status": MarketStatus.EARLY_CLOSE,
        "early_close_time": EARLY_CLOSE,
        "late_open_time": None,
        "extended_close_time": EARLY_EXTENDED_CLOSE,
    }


_NYD = "New Year's Day"
_MLK = "Martin Luther King, Jr. Day"
_WASH = "Washington's Birthday"
_GF = "Good Friday"
_MEM = "Memorial Day"
_JUNE = "Juneteenth National Independence Day"
_JULY4 = "Independence Day"
_LABOR = "Labor Day"
_THANKS = "Thanksgiving Day"
_XMAS = "Christmas Day"
_DAY_AFTER_THANKS = "Day after Thanksgiving"
_XMAS_EVE = "Christmas Eve"
_JULY3 = "Day before Independence Day"

_ROWS: Final[tuple[dict[str, Any], ...]] = (
    # 2020
    _closed(date(2020, 1, 1), _NYD),
    _closed(date(2020, 1, 20), _MLK),
    _closed(date(2020, 2, 17), _WASH),
    _closed(date(2020, 4, 10), _GF),
    _closed(date(2020, 5, 25), _MEM),
    _closed(date(2020, 7, 3), f"{_JULY4} (observed)"),
    _closed(date(2020, 9, 7), _LABOR),
    _closed(date(2020, 11, 26), _THANKS),
    _early(date(2020, 11, 27), _DAY_AFTER_THANKS),
    _early(date(2020, 12, 24), _XMAS_EVE),
    _closed(date(2020, 12, 25), _XMAS),
    # 2021 (no Juneteenth closure yet; no New Year's observance on 2021-12-31)
    _closed(date(2021, 1, 1), _NYD),
    _closed(date(2021, 1, 18), _MLK),
    _closed(date(2021, 2, 15), _WASH),
    _closed(date(2021, 4, 2), _GF),
    _closed(date(2021, 5, 31), _MEM),
    _closed(date(2021, 7, 5), f"{_JULY4} (observed)"),
    _closed(date(2021, 9, 6), _LABOR),
    _closed(date(2021, 11, 25), _THANKS),
    _early(date(2021, 11, 26), _DAY_AFTER_THANKS),
    _closed(date(2021, 12, 24), f"{_XMAS} (observed)"),
    # 2022 (New Year's Day on a Saturday: no holiday observed)
    _closed(date(2022, 1, 17), _MLK),
    _closed(date(2022, 2, 21), _WASH),
    _closed(date(2022, 4, 15), _GF),
    _closed(date(2022, 5, 30), _MEM),
    _closed(date(2022, 6, 20), f"{_JUNE} (observed)"),
    _closed(date(2022, 7, 4), _JULY4),
    _closed(date(2022, 9, 5), _LABOR),
    _closed(date(2022, 11, 24), _THANKS),
    _early(date(2022, 11, 25), _DAY_AFTER_THANKS),
    _closed(date(2022, 12, 26), f"{_XMAS} (observed)"),
    # 2023
    _closed(date(2023, 1, 2), f"{_NYD} (observed)"),
    _closed(date(2023, 1, 16), _MLK),
    _closed(date(2023, 2, 20), _WASH),
    _closed(date(2023, 4, 7), _GF),
    _closed(date(2023, 5, 29), _MEM),
    _closed(date(2023, 6, 19), _JUNE),
    _early(date(2023, 7, 3), _JULY3),
    _closed(date(2023, 7, 4), _JULY4),
    _closed(date(2023, 9, 4), _LABOR),
    _closed(date(2023, 11, 23), _THANKS),
    _early(date(2023, 11, 24), _DAY_AFTER_THANKS),
    _closed(date(2023, 12, 25), _XMAS),
    # 2024
    _closed(date(2024, 1, 1), _NYD),
    _closed(date(2024, 1, 15), _MLK),
    _closed(date(2024, 2, 19), _WASH),
    _closed(date(2024, 3, 29), _GF),
    _closed(date(2024, 5, 27), _MEM),
    _closed(date(2024, 6, 19), _JUNE),
    _early(date(2024, 7, 3), _JULY3),
    _closed(date(2024, 7, 4), _JULY4),
    _closed(date(2024, 9, 2), _LABOR),
    _closed(date(2024, 11, 28), _THANKS),
    _early(date(2024, 11, 29), _DAY_AFTER_THANKS),
    _early(date(2024, 12, 24), _XMAS_EVE),
    _closed(date(2024, 12, 25), _XMAS),
    # 2025
    _closed(date(2025, 1, 1), _NYD),
    _closed(date(2025, 1, 9), "National Day of Mourning (President Carter)"),
    _closed(date(2025, 1, 20), _MLK),
    _closed(date(2025, 2, 17), _WASH),
    _closed(date(2025, 4, 18), _GF),
    _closed(date(2025, 5, 26), _MEM),
    _closed(date(2025, 6, 19), _JUNE),
    _early(date(2025, 7, 3), _JULY3),
    _closed(date(2025, 7, 4), _JULY4),
    _closed(date(2025, 9, 1), _LABOR),
    _closed(date(2025, 11, 27), _THANKS),
    _early(date(2025, 11, 28), _DAY_AFTER_THANKS),
    _early(date(2025, 12, 24), _XMAS_EVE),
    _closed(date(2025, 12, 25), _XMAS),
    # 2026
    _closed(date(2026, 1, 1), _NYD),
    _closed(date(2026, 1, 19), _MLK),
    _closed(date(2026, 2, 16), _WASH),
    _closed(date(2026, 4, 3), _GF),
    _closed(date(2026, 5, 25), _MEM),
    _closed(date(2026, 6, 19), _JUNE),
    _closed(date(2026, 7, 3), f"{_JULY4} (observed)"),
    _closed(date(2026, 9, 7), _LABOR),
    _closed(date(2026, 11, 26), _THANKS),
    _early(date(2026, 11, 27), _DAY_AFTER_THANKS),
    _early(date(2026, 12, 24), _XMAS_EVE),
    _closed(date(2026, 12, 25), _XMAS),
    # 2027
    _closed(date(2027, 1, 1), _NYD),
    _closed(date(2027, 1, 18), _MLK),
    _closed(date(2027, 2, 15), _WASH),
    _closed(date(2027, 3, 26), _GF),
    _closed(date(2027, 5, 31), _MEM),
    _closed(date(2027, 6, 18), f"{_JUNE} (observed)"),
    _closed(date(2027, 7, 5), f"{_JULY4} (observed)"),
    _closed(date(2027, 9, 6), _LABOR),
    _closed(date(2027, 11, 25), _THANKS),
    _early(date(2027, 11, 26), _DAY_AFTER_THANKS),
    _closed(date(2027, 12, 24), f"{_XMAS} (observed)"),
    # 2028 (New Year's Day on a Saturday: no holiday observed)
    _closed(date(2028, 1, 17), _MLK),
    _closed(date(2028, 2, 21), _WASH),
    _closed(date(2028, 4, 14), _GF),
    _closed(date(2028, 5, 29), _MEM),
    _closed(date(2028, 6, 19), _JUNE),
    _early(date(2028, 7, 3), _JULY3),
    _closed(date(2028, 7, 4), _JULY4),
    _closed(date(2028, 9, 4), _LABOR),
    _closed(date(2028, 11, 23), _THANKS),
    _early(date(2028, 11, 24), _DAY_AFTER_THANKS),
    _closed(date(2028, 12, 25), _XMAS),
)


def us_equity_holidays(calendar_id: str) -> list[dict[str, Any]]:
    """Every ``trading_holidays`` row for ``calendar_id`` (NYSE or NASDAQ)."""
    if calendar_id not in US_EQUITY_CALENDAR_IDS:
        raise ValueError(f"not a US equity calendar: {calendar_id!r}")
    return [{**row, "calendar_id": calendar_id} for row in _ROWS]
