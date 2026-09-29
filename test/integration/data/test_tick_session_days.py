"""``session_days`` on the real ``CME_EQUITY`` calendar (slice 223).

The two free-credit jobs' ranges must yield 26 and 52 days: adoption creates
one unit per session day, and each job holds one file per such day (LLD 224
Functional Requirement 2).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from manta_trading.data.base.trading_calendar import (
    OutOfPopulatedRangeError,
    TradingCalendar,
)
from manta_trading.data.tick.session_days import session_days
from manta_trading.market.schema.seed_cme_calendar import CME_EQUITY_CALENDAR_ID


@pytest.fixture()
def cme(session_migrated_db: str) -> Iterator[TradingCalendar]:
    calendar = TradingCalendar(CME_EQUITY_CALENDAR_ID, session_migrated_db)
    yield calendar
    calendar.close()


def test_trades_job_range_has_26_days(cme: TradingCalendar) -> None:
    days = session_days(cme, date(2024, 8, 30), date(2024, 9, 30))
    assert len(days) == 26
    assert date(2024, 8, 31) not in days  # Saturday


def test_tbbo_job_range_has_52_days(cme: TradingCalendar) -> None:
    days = session_days(cme, date(2024, 11, 1), date(2025, 1, 1))
    assert len(days) == 52


def test_christmas_is_touched_by_the_next_session(cme: TradingCalendar) -> None:
    days = session_days(cme, date(2024, 12, 24), date(2024, 12, 27))
    assert date(2024, 12, 25) in days


def test_range_before_the_calendar_raises(cme: TradingCalendar) -> None:
    with pytest.raises(OutOfPopulatedRangeError):
        session_days(cme, date(2019, 12, 1), date(2019, 12, 5))
