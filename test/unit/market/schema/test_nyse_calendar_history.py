"""The explicit NYSE/NASDAQ holiday table (slice 174 task 2.2)."""

from __future__ import annotations

from collections import Counter
from datetime import date, time

import pytest

from manta_trading.data.base.trading_calendar import MarketStatus
from manta_trading.market.schema.nyse_calendar_history import (
    NYSE_HOLIDAYS_SEEDED_THROUGH,
    us_equity_holidays,
)
from manta_trading.market.schema.nyse_session_changes import (
    EXPECTED_SESSION_CHANGES,
)
from manta_trading.market.schema.seed_calendar import generate_holidays

ROWS = us_equity_holidays("NYSE")
CLOSED = {r["holiday_date"] for r in ROWS if r["market_status"] is MarketStatus.CLOSED}
EARLY = [r for r in ROWS if r["market_status"] is MarketStatus.EARLY_CLOSE]


def test_known_defects_fixed() -> None:
    assert date(2021, 12, 31) not in {r["holiday_date"] for r in ROWS}
    assert date(2025, 1, 9) in CLOSED
    early_dates = {r["holiday_date"] for r in EARLY}
    assert date(2020, 7, 2) not in early_dates
    assert date(2026, 7, 2) not in early_dates


@pytest.mark.parametrize("year", [2027, 2028])
def test_future_years_have_closures(year: int) -> None:
    # 2028 observes no New Year's Day (Saturday), so one fewer.
    expected = 10 if year == 2027 else 9
    assert sum(1 for d in CLOSED if d.year == year) == expected


def test_every_early_close_has_extended_close() -> None:
    assert EARLY
    for row in EARLY:
        assert row["early_close_time"] == time(13, 0)
        assert row["extended_close_time"] == time(17, 0)
    for row in ROWS:
        if row["market_status"] is MarketStatus.CLOSED:
            assert row["extended_close_time"] is None


def test_no_weekend_or_duplicate_dates() -> None:
    dates = [r["holiday_date"] for r in ROWS]
    assert all(d.weekday() < 5 for d in dates)
    assert not [d for d, n in Counter(dates).items() if n > 1]
    assert max(dates) <= NYSE_HOLIDAYS_SEEDED_THROUGH
    assert min(dates).year == 2020


def test_nasdaq_rows_equal_nyse_apart_from_id() -> None:
    nasdaq = us_equity_holidays("NASDAQ")
    assert {r["calendar_id"] for r in nasdaq} == {"NASDAQ"}
    strip = [{k: v for k, v in r.items() if k != "calendar_id"} for r in ROWS]
    assert strip == [{k: v for k, v in r.items() if k != "calendar_id"} for r in nasdaq]


def test_unknown_calendar_raises() -> None:
    with pytest.raises(ValueError, match="CME_EQUITY"):
        us_equity_holidays("CME_EQUITY")


def test_difference_from_rule_generator_is_the_four_known_dates() -> None:
    def keyed(rows: list[dict]) -> set[tuple[date, str, object]]:
        return {
            (r["holiday_date"], str(r["market_status"]), r["early_close_time"])
            for r in rows
            if r["holiday_date"].year <= 2026
        }

    old = generate_holidays("NYSE", 2020, 2026)
    for row in old:  # the generator renders times as text
        if row["early_close_time"]:
            row["early_close_time"] = time.fromisoformat(row["early_close_time"])
    new_rows, old_rows = keyed(ROWS), keyed(old)
    assert {d for d, *_ in new_rows - old_rows} == {date(2025, 1, 9)}
    assert {d for d, *_ in old_rows - new_rows} == {
        date(2020, 7, 2),
        date(2021, 12, 31),
        date(2026, 7, 2),
    }


def test_future_rows_match_expected_session_changes() -> None:
    future_closed = {d for d in CLOSED if d.year >= 2027}
    future_early = {r["holiday_date"] for r in EARLY if r["holiday_date"].year >= 2027}
    assert future_closed == {
        d for d in EXPECTED_SESSION_CHANGES.closed if d.year >= 2027
    }
    assert future_early == {
        d for d in EXPECTED_SESSION_CHANGES.retimed if d.year >= 2027
    }
