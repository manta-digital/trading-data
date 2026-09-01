"""Calendar seed data generation for NYSE and NASDAQ.

Generates holiday schedules (2020-2026) including full closures and early
closes.  All SQL output uses ON CONFLICT DO NOTHING for idempotency.
"""

from __future__ import annotations

from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Calendar metadata
# ---------------------------------------------------------------------------

NYSE_CALENDAR: dict = {
    "calendar_id": "NYSE",
    "exchange_name": "New York Stock Exchange",
    "timezone": "America/New_York",
    "market_open": "09:30",
    "market_close": "16:00",
    "extended_open": "04:00",
    "extended_close": "20:00",
    "has_extended_hours": True,
}

NASDAQ_CALENDAR: dict = {
    "calendar_id": "NASDAQ",
    "exchange_name": "NASDAQ Stock Market",
    "timezone": "America/New_York",
    "market_open": "09:30",
    "market_close": "16:00",
    "extended_open": "04:00",
    "extended_close": "20:00",
    "has_extended_hours": True,
}


# ---------------------------------------------------------------------------
# Easter / Good Friday computation
# ---------------------------------------------------------------------------


def compute_easter(year: int) -> date:
    """Compute Easter Sunday using the Anonymous Gregorian (Butcher's) algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


# ---------------------------------------------------------------------------
# Holiday generation helpers
# ---------------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the *n*-th occurrence of *weekday* (0=Mon) in *month*."""
    first = date(year, month, 1)
    # Days until first occurrence of target weekday
    offset = (weekday - first.weekday()) % 7
    result = first + timedelta(days=offset + 7 * (n - 1))
    return result


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of *weekday* (0=Mon) in *month*."""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _weekend_adjust(d: date) -> date:
    """Shift Saturday to Friday, Sunday to Monday."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def _holiday(
    calendar_id: str,
    d: date,
    name: str,
    status: str = "closed",
    early_close_time: str | None = None,
    late_open_time: str | None = None,
) -> dict:
    """Build a single holiday dict."""
    return {
        "calendar_id": calendar_id,
        "holiday_date": d,
        "holiday_name": name,
        "market_status": status,
        "early_close_time": early_close_time,
        "late_open_time": late_open_time,
    }


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------


def generate_holidays(
    calendar_id: str,
    start_year: int,
    end_year: int,
) -> list[dict]:
    """Generate holiday entries for a US equity exchange.

    Covers NYSE/NASDAQ full closures and standard early-close days
    from *start_year* through *end_year* (inclusive).
    """
    holidays: list[dict] = []

    for year in range(start_year, end_year + 1):
        # --- Fixed-date holidays with weekend adjustment --------------
        new_years = _weekend_adjust(date(year, 1, 1))
        holidays.append(_holiday(calendar_id, new_years, "New Year's Day"))

        if year >= 2022:
            juneteenth = _weekend_adjust(date(year, 6, 19))
            holidays.append(_holiday(calendar_id, juneteenth, "Juneteenth"))

        july4 = _weekend_adjust(date(year, 7, 4))
        holidays.append(_holiday(calendar_id, july4, "Independence Day"))

        christmas = _weekend_adjust(date(year, 12, 25))
        holidays.append(_holiday(calendar_id, christmas, "Christmas Day"))

        # --- Relative holidays ----------------------------------------
        mlk = _nth_weekday(year, 1, 0, 3)  # 3rd Monday Jan
        holidays.append(_holiday(calendar_id, mlk, "MLK Day"))

        presidents = _nth_weekday(year, 2, 0, 3)  # 3rd Monday Feb
        holidays.append(_holiday(calendar_id, presidents, "Presidents' Day"))

        memorial = _last_weekday(year, 5, 0)  # Last Monday May
        holidays.append(_holiday(calendar_id, memorial, "Memorial Day"))

        labor = _nth_weekday(year, 9, 0, 1)  # 1st Monday Sep
        holidays.append(_holiday(calendar_id, labor, "Labor Day"))

        thanksgiving = _nth_weekday(year, 11, 3, 4)  # 4th Thursday Nov
        holidays.append(_holiday(calendar_id, thanksgiving, "Thanksgiving"))

        # --- Good Friday (Friday before Easter Sunday) ----------------
        easter = compute_easter(year)
        good_friday = easter - timedelta(days=2)
        holidays.append(_holiday(calendar_id, good_friday, "Good Friday"))

        # --- Early close days -----------------------------------------
        # Day before Independence Day (13:00) if weekday
        day_before_july4 = july4 - timedelta(days=1)
        if day_before_july4.weekday() < 5:
            holidays.append(
                _holiday(
                    calendar_id,
                    day_before_july4,
                    "Day Before Independence Day",
                    status="early_close",
                    early_close_time="13:00",
                )
            )

        # Black Friday (day after Thanksgiving, always a Friday)
        black_friday = thanksgiving + timedelta(days=1)
        holidays.append(
            _holiday(
                calendar_id,
                black_friday,
                "Black Friday",
                status="early_close",
                early_close_time="13:00",
            )
        )

        # Christmas Eve (13:00) if weekday and not weekend-adjacent
        christmas_eve = date(year, 12, 24)
        if christmas_eve.weekday() < 5:
            # Skip if Christmas itself was adjusted to the 24th (Sat→Fri)
            if christmas != christmas_eve:
                holidays.append(
                    _holiday(
                        calendar_id,
                        christmas_eve,
                        "Christmas Eve",
                        status="early_close",
                        early_close_time="13:00",
                    )
                )

    return holidays


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------


def generate_calendar_insert_sql(calendar: dict) -> str:
    """Return an INSERT statement for a single trading_calendars row."""
    name = _sql_escape(calendar["exchange_name"])
    return (
        "INSERT INTO trading_calendars "
        "(calendar_id, exchange_name, timezone, market_open, market_close, "
        "extended_open, extended_close, has_extended_hours) VALUES ("
        f"'{calendar['calendar_id']}', "
        f"'{name}', "
        f"'{calendar['timezone']}', "
        f"'{calendar['market_open']}', "
        f"'{calendar['market_close']}', "
        f"'{calendar['extended_open']}', "
        f"'{calendar['extended_close']}', "
        f"{str(calendar['has_extended_hours']).upper()}"
        ") ON CONFLICT DO NOTHING;"
    )


def _sql_escape(value: str) -> str:
    """Escape single quotes for SQL string literals."""
    return value.replace("'", "''")


def generate_holidays_insert_sql(holidays: list[dict]) -> str:
    """Return a multi-row INSERT for trading_holidays with ON CONFLICT DO NOTHING."""
    if not holidays:
        return "-- No holidays to insert"

    rows: list[str] = []
    for h in holidays:
        early = f"'{h['early_close_time']}'" if h["early_close_time"] else "NULL"
        late = f"'{h['late_open_time']}'" if h["late_open_time"] else "NULL"
        name = _sql_escape(h["holiday_name"])
        rows.append(
            f"  ('{h['calendar_id']}', '{h['holiday_date']}', "
            f"'{name}', '{h['market_status']}', "
            f"{early}, {late})"
        )

    values = ",\n".join(rows)
    return (
        "INSERT INTO trading_holidays "
        "(calendar_id, holiday_date, holiday_name, market_status, "
        "early_close_time, late_open_time) VALUES\n"
        f"{values}\n"
        "ON CONFLICT DO NOTHING;"
    )
