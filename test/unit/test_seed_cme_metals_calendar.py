"""Unit tests for the CME_METALS seed table (232 TD1, TD2). No DB."""

from __future__ import annotations

import inspect
import re
from datetime import date, time

import manta_trading.market.schema.seed_cme_metals_calendar as seed
from manta_trading.data.base.trading_calendar import MarketStatus
from manta_trading.market.schema.seed_cme_calendar import cme_calendar_insert
from manta_trading.market.schema.seed_cme_metals_calendar import (
    CME_METALS_CALENDAR_ID,
    CME_METALS_EXCEPTIONS,
    CME_METALS_HOLIDAYS_SEEDED_THROUGH,
    CME_METALS_SEED,
    CME_METALS_SEED_START,
)

_REGULAR_CLOSE = time(16, 0)
_ENTRY_START = re.compile(r"^\s+_(closed|early_close)\(")
_TABLE_START = "CME_METALS_EXCEPTIONS:"


def _entries_without_source(lines: list[str]) -> tuple[int, list[str]]:
    """Count table entries, and list those not under a ``# source:`` block."""
    start = next(i for i, text in enumerate(lines) if _TABLE_START in text)
    entries, missing = 0, []
    for i, line in enumerate(lines[start:], start=start):
        if not _ENTRY_START.match(line):
            continue
        entries += 1
        j, block = i - 1, []
        while lines[j].lstrip().startswith("#"):
            block.append(lines[j].strip())
            j -= 1
        if not any(c.startswith("# source:") for c in block):
            missing.append(line.strip())
    return entries, missing


def _row(day: date) -> dict[str, object]:
    (row,) = [r for r in CME_METALS_EXCEPTIONS if r["holiday_date"] == day]
    return row


def test_dates_are_unique_sorted_weekdays_in_seed_range() -> None:
    dates = [row["holiday_date"] for row in CME_METALS_EXCEPTIONS]
    assert len(dates) == len(set(dates))
    assert dates == sorted(dates)
    for day in dates:
        assert day.weekday() < 5, day
        assert CME_METALS_SEED_START <= day <= CME_METALS_HOLIDAYS_SEEDED_THROUGH


def test_statuses_and_times_are_consistent() -> None:
    for row in CME_METALS_EXCEPTIONS:
        status = MarketStatus(row["market_status"])
        assert row["late_open_time"] is None, row
        if status == MarketStatus.CLOSED:
            assert row["early_close_time"] is None, row
        else:
            assert status == MarketStatus.EARLY_CLOSE, row
            assert row["early_close_time"] < _REGULAR_CLOSE, row


def test_every_entry_is_preceded_by_a_source_comment() -> None:
    entries, missing = _entries_without_source(inspect.getsource(seed).splitlines())
    assert entries == len(CME_METALS_EXCEPTIONS)
    assert missing == []


def test_the_source_check_catches_a_deleted_source_comment() -> None:
    lines = inspect.getsource(seed).splitlines()
    first = next(
        i for i, text in enumerate(lines) if text.lstrip().startswith("# source:")
    )
    mutated = [text for i, text in enumerate(lines) if i != first]
    _, missing = _entries_without_source(mutated)
    assert len(missing) == 1


def test_calendar_row_matches_td1() -> None:
    _, params = cme_calendar_insert(CME_METALS_SEED)
    assert params == {
        "calendar_id": CME_METALS_CALENDAR_ID,
        "exchange_name": "CME Globex Metals",
        "timezone": "America/Chicago",
        "market_open": time(17, 0),
        "market_close": time(16, 0),
        "extended_open": None,
        "extended_close": None,
        "has_extended_hours": False,
        "holidays_seeded_through": date(2027, 12, 31),
    }
    assert CME_METALS_CALENDAR_ID == "CME_METALS"
    assert CME_METALS_SEED_START == date(2020, 1, 1)


def test_purchased_range_exceptions() -> None:
    """The rows TD7's too-wide check reads on the bought GC range."""
    assert _row(date(2024, 11, 28))["early_close_time"] == time(13, 30)
    assert _row(date(2024, 11, 29))["early_close_time"] == time(13, 45)
    assert _row(date(2024, 12, 24))["early_close_time"] == time(12, 45)
    for day in (date(2024, 12, 25), date(2025, 1, 1)):
        assert _row(day)["market_status"] == MarketStatus.CLOSED
