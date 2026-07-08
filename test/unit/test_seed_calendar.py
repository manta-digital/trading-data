"""Unit tests for calendar seed data generation."""

from __future__ import annotations

from datetime import date

import pytest

from manta_trading.market.schema.seed_calendar import (
    compute_easter,
    generate_calendar_insert_sql,
    generate_holidays,
    generate_holidays_insert_sql,
    NYSE_CALENDAR,
    NASDAQ_CALENDAR,
)


# ---------------------------------------------------------------------------
# Easter / Good Friday
# ---------------------------------------------------------------------------


class TestComputeEaster:
    @pytest.mark.parametrize(
        "year, expected",
        [
            (2020, date(2020, 4, 12)),
            (2021, date(2021, 4, 4)),
            (2024, date(2024, 3, 31)),
            (2025, date(2025, 4, 20)),
            (2026, date(2026, 4, 5)),
        ],
    )
    def test_known_years(self, year: int, expected: date):
        assert compute_easter(year) == expected

    @pytest.mark.parametrize(
        "year, expected_good_friday",
        [
            (2020, date(2020, 4, 10)),
            (2021, date(2021, 4, 2)),
            (2024, date(2024, 3, 29)),
            (2025, date(2025, 4, 18)),
            (2026, date(2026, 4, 3)),
        ],
    )
    def test_good_friday(self, year: int, expected_good_friday: date):
        from datetime import timedelta

        easter = compute_easter(year)
        assert easter - timedelta(days=2) == expected_good_friday


# ---------------------------------------------------------------------------
# Relative holidays
# ---------------------------------------------------------------------------


class TestRelativeHolidays:
    def _find(self, holidays: list[dict], name: str) -> dict | None:
        matches = [h for h in holidays if h["holiday_name"] == name]
        return matches[0] if matches else None

    def test_thanksgiving_2024(self):
        holidays = generate_holidays("NYSE", 2024, 2024)
        h = self._find(holidays, "Thanksgiving")
        assert h is not None
        assert h["holiday_date"] == date(2024, 11, 28)

    def test_mlk_day_2024(self):
        holidays = generate_holidays("NYSE", 2024, 2024)
        h = self._find(holidays, "MLK Day")
        assert h is not None
        assert h["holiday_date"] == date(2024, 1, 15)

    def test_memorial_day_2024(self):
        holidays = generate_holidays("NYSE", 2024, 2024)
        h = self._find(holidays, "Memorial Day")
        assert h is not None
        assert h["holiday_date"] == date(2024, 5, 27)


# ---------------------------------------------------------------------------
# Weekend adjustment
# ---------------------------------------------------------------------------


class TestWeekendAdjustment:
    def test_july4_saturday_2020(self):
        """July 4, 2020 is Saturday — observed Friday July 3."""
        holidays = generate_holidays("NYSE", 2020, 2020)
        h = next(h for h in holidays if h["holiday_name"] == "Independence Day")
        assert h["holiday_date"] == date(2020, 7, 3)

    def test_july4_sunday_2021(self):
        """July 4, 2021 is Sunday — observed Monday July 5."""
        holidays = generate_holidays("NYSE", 2021, 2021)
        h = next(h for h in holidays if h["holiday_name"] == "Independence Day")
        assert h["holiday_date"] == date(2021, 7, 5)


# ---------------------------------------------------------------------------
# Juneteenth
# ---------------------------------------------------------------------------


class TestJuneteenth:
    def test_not_present_before_2022(self):
        holidays = generate_holidays("NYSE", 2020, 2021)
        juneteenth = [h for h in holidays if h["holiday_name"] == "Juneteenth"]
        assert juneteenth == []

    def test_present_from_2022(self):
        for year in (2022, 2023, 2024, 2025, 2026):
            holidays = generate_holidays("NYSE", year, year)
            juneteenth = [h for h in holidays if h["holiday_name"] == "Juneteenth"]
            assert len(juneteenth) == 1, f"Expected Juneteenth in {year}"


# ---------------------------------------------------------------------------
# Early close days
# ---------------------------------------------------------------------------


class TestEarlyClose:
    def test_black_friday_exists(self):
        holidays = generate_holidays("NYSE", 2024, 2024)
        bf = next(h for h in holidays if h["holiday_name"] == "Black Friday")
        assert bf["market_status"] == "early_close"
        assert bf["early_close_time"] == "13:00"
        # Black Friday 2024 = Nov 29
        assert bf["holiday_date"] == date(2024, 11, 29)

    def test_day_before_july4_early_close(self):
        holidays = generate_holidays("NYSE", 2024, 2024)
        dbj4 = [h for h in holidays
                if h["holiday_name"] == "Day Before Independence Day"]
        assert len(dbj4) == 1
        assert dbj4[0]["market_status"] == "early_close"
        assert dbj4[0]["early_close_time"] == "13:00"

    def test_christmas_eve_early_close(self):
        holidays = generate_holidays("NYSE", 2024, 2024)
        ce = [h for h in holidays if h["holiday_name"] == "Christmas Eve"]
        assert len(ce) == 1
        assert ce[0]["market_status"] == "early_close"
        assert ce[0]["early_close_time"] == "13:00"


# ---------------------------------------------------------------------------
# Holiday count
# ---------------------------------------------------------------------------


class TestHolidayCount:
    def test_nyse_2020_2026_range(self):
        holidays = generate_holidays("NYSE", 2020, 2026)
        assert 80 <= len(holidays) <= 100


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------


class TestSqlGeneration:
    def test_calendar_insert_sql(self):
        sql = generate_calendar_insert_sql(NYSE_CALENDAR)
        assert "INSERT INTO trading_calendars" in sql
        assert "ON CONFLICT DO NOTHING" in sql
        assert "'NYSE'" in sql
        assert "'09:30'" in sql
        assert "'16:00'" in sql

    def test_holidays_insert_sql(self):
        holidays = generate_holidays("NYSE", 2024, 2024)
        sql = generate_holidays_insert_sql(holidays)
        assert "INSERT INTO trading_holidays" in sql
        assert "ON CONFLICT DO NOTHING" in sql
        assert "'NYSE'" in sql
        assert "'Thanksgiving'" in sql

    def test_holidays_insert_sql_empty(self):
        sql = generate_holidays_insert_sql([])
        assert "No holidays" in sql

    def test_nasdaq_calendar_metadata(self):
        sql = generate_calendar_insert_sql(NASDAQ_CALENDAR)
        assert "'NASDAQ'" in sql
        assert "'NASDAQ Stock Market'" in sql

    def test_holidays_sql_escapes_apostrophes(self):
        """New Year's Day contains an apostrophe — must be escaped."""
        holidays = generate_holidays("NYSE", 2024, 2024)
        sql = generate_holidays_insert_sql(holidays)
        assert "New Year''s Day" in sql
        # No unescaped single-apostrophe-s pattern
        assert "New Year's Day" not in sql
