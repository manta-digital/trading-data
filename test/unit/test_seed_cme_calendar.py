"""Unit tests for the CME_EQUITY seed table (221 D5). No DB."""

from __future__ import annotations

import inspect
import re
from datetime import time

import manta_trading.market.schema.seed_cme_calendar as seed
from manta_trading.data.base.trading_calendar import MarketStatus
from manta_trading.market.schema.seed_cme_calendar import (
    CME_EQUITY_CALENDAR_ID,
    CME_EQUITY_EXCEPTIONS,
    CME_EQUITY_HOLIDAYS_SEEDED_THROUGH,
    CME_EQUITY_SEED_START,
    cme_equity_calendar_insert,
    cme_equity_holidays_insert,
)

_REGULAR_CLOSE = time(16, 0)
_ENTRY_START = re.compile(r"^\s+_(closed|early_close)\(")


def test_dates_are_unique_weekdays_in_seed_range() -> None:
    dates = [row["holiday_date"] for row in CME_EQUITY_EXCEPTIONS]
    assert len(dates) == len(set(dates))
    assert dates == sorted(dates)
    for day in dates:
        assert day.weekday() < 5, day
        assert CME_EQUITY_SEED_START <= day <= CME_EQUITY_HOLIDAYS_SEEDED_THROUGH


def test_statuses_and_times_are_consistent() -> None:
    for row in CME_EQUITY_EXCEPTIONS:
        status = MarketStatus(row["market_status"])
        assert row["late_open_time"] is None, row
        if status == MarketStatus.CLOSED:
            assert row["early_close_time"] is None, row
        else:
            assert status == MarketStatus.EARLY_CLOSE, row
            assert row["early_close_time"] < _REGULAR_CLOSE, row


def test_every_entry_is_preceded_by_a_source_comment() -> None:
    """Reading the module source: each entry sits under a ``# source:`` block."""
    lines = inspect.getsource(seed).splitlines()
    table_start = next(
        i for i, text in enumerate(lines) if "CME_EQUITY_EXCEPTIONS:" in text
    )
    entries = 0
    for i, line in enumerate(lines[table_start:], start=table_start):
        if not _ENTRY_START.match(line):
            continue
        entries += 1
        j = i - 1
        comment_block = []
        while lines[j].lstrip().startswith("#"):
            comment_block.append(lines[j].strip())
            j -= 1
        assert any(c.startswith("# source:") for c in comment_block), line
    assert entries == len(CME_EQUITY_EXCEPTIONS)


def test_calendar_insert_columns() -> None:
    sql, params = cme_equity_calendar_insert()
    assert sql.startswith(
        "INSERT INTO trading_calendars (calendar_id, exchange_name, timezone, "
        "market_open, market_close, extended_open, extended_close, "
        "has_extended_hours, holidays_seeded_through) VALUES"
    )
    assert sql.endswith("ON CONFLICT DO NOTHING")
    assert params["calendar_id"] == CME_EQUITY_CALENDAR_ID
    assert params["market_open"] == time(17, 0)
    assert params["market_close"] == time(16, 0)
    assert params["holidays_seeded_through"] == CME_EQUITY_HOLIDAYS_SEEDED_THROUGH


def test_holidays_insert_columns() -> None:
    sql, rows = cme_equity_holidays_insert()
    assert sql.startswith(
        "INSERT INTO trading_holidays (calendar_id, holiday_date, holiday_name, "
        "market_status, early_close_time, late_open_time) VALUES"
    )
    assert sql.endswith("ON CONFLICT DO NOTHING")
    assert len(rows) == len(CME_EQUITY_EXCEPTIONS)
    assert {r["calendar_id"] for r in rows} == {CME_EQUITY_CALENDAR_ID}
    assert all(isinstance(r["market_status"], str) for r in rows)
