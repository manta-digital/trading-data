"""Unit tests for the pre-2020 CME exception seeds (301 D4). No DB."""

from __future__ import annotations

import inspect
import re
from datetime import date
from types import ModuleType
from typing import Any

import pytest

import manta_trading.market.schema.seed_cme_equity_history as equity
import manta_trading.market.schema.seed_cme_metals_history as metals
from manta_trading.data.base.trading_calendar import MarketStatus
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START

_ENTRY_START = re.compile(r"^\s+(closed_day|early_close_day|late_open_day)\($")
_ENTRY_START_INLINE = re.compile(r"^\s+(closed_day|early_close_day|late_open_day)\(")
_UNTOUCHED_FROM = date(2020, 1, 1)
_SEEDS = [
    pytest.param(equity, equity.CME_EQUITY_HISTORY_EXCEPTIONS, id="equity"),
    pytest.param(metals, metals.CME_METALS_HISTORY_EXCEPTIONS, id="metals"),
]


def _entries_without_source(module: ModuleType) -> tuple[int, list[str]]:
    """Count table entries, and list those not under a ``# source:`` block."""
    lines = inspect.getsource(module).splitlines()
    entries, missing = 0, []
    for i, line in enumerate(lines):
        if not (_ENTRY_START.match(line) or _ENTRY_START_INLINE.match(line)):
            continue
        entries += 1
        j, block = i - 1, []
        while lines[j].lstrip().startswith("#"):
            block.append(lines[j].strip())
            j -= 1
        if not any(c.startswith("# source:") for c in block) or not any(
            c.startswith("# https://") for c in block
        ):
            missing.append(line.strip())
    return entries, missing


@pytest.mark.parametrize(("module", "rows"), _SEEDS)
def test_every_row_cites_a_dated_document(
    module: ModuleType, rows: tuple[dict[str, Any], ...]
) -> None:
    entries, missing = _entries_without_source(module)
    assert entries == len(rows)
    assert missing == []


@pytest.mark.parametrize(("module", "rows"), _SEEDS)
def test_rows_are_inside_the_history_range(
    module: ModuleType, rows: tuple[dict[str, Any], ...]
) -> None:
    outside = [
        r["holiday_date"]
        for r in rows
        if not CME_DATASET_START <= r["holiday_date"] < _UNTOUCHED_FROM
    ]
    assert outside == []


@pytest.mark.parametrize(("module", "rows"), _SEEDS)
def test_no_date_repeats_and_rows_are_in_order(
    module: ModuleType, rows: tuple[dict[str, Any], ...]
) -> None:
    dates = [r["holiday_date"] for r in rows]
    assert dates == sorted(set(dates))


@pytest.mark.parametrize(("module", "rows"), _SEEDS)
def test_rows_carry_the_times_their_status_needs(
    module: ModuleType, rows: tuple[dict[str, Any], ...]
) -> None:
    for row in rows:
        status = row["market_status"]
        if status == MarketStatus.CLOSED:
            assert row["early_close_time"] is None is row["late_open_time"], row
        elif status == MarketStatus.EARLY_CLOSE:
            assert row["early_close_time"] is not None, row
        else:
            assert row["late_open_time"] is not None, row


def test_metals_never_opens_late() -> None:
    """Every metals reopen in the documents is the regular 17:00."""
    assert all(
        r["market_status"] != MarketStatus.LATE_OPEN
        for r in metals.CME_METALS_HISTORY_EXCEPTIONS
    )
