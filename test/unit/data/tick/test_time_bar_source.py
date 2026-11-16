"""Slice 309: source runs and the served plan, on hand-built plans."""

from __future__ import annotations

from datetime import date

from manta_trading.data.tick.bar_constants import TICKS_SOURCE
from manta_trading.data.tick.get_plan import ContractTarget, GetPlan, Incomplete
from manta_trading.data.tick.tick_reads import TickWindow
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.time_bar_source import SourceRun, served_plan, source_runs

D1, D2, D3, D4 = (date(2024, 12, d) for d in (2, 3, 4, 5))
BARS = "ohlcv-1m"


def _plan(*days: date, incomplete: tuple[Incomplete, ...] = ()) -> GetPlan:
    windows = tuple(TickWindow(d, 1, "ESZ4", 0, 1) for d in days)
    return GetPlan(
        ContractTarget("ESZ4", 1, "ES"),
        days[0],
        days[-1],
        windows,
        (),
        (),
        incomplete,
        None,
    )


def test_one_source_across_the_range_is_one_run() -> None:
    plan = _plan(D1, D2, D3)
    sources = dict.fromkeys((D1, D2, D3), TICKS_SOURCE)
    assert source_runs(plan, sources, {D1, D2, D3}) == [SourceRun(D1, D3, TICKS_SOURCE)]


def test_a_switch_of_source_starts_a_run() -> None:
    plan = _plan(D1, D2, D3)
    sources = {D1: BARS, D2: BARS, D3: TICKS_SOURCE}
    assert source_runs(plan, sources, {D1, D2, D3}) == [
        SourceRun(D1, D2, BARS),
        SourceRun(D3, D3, TICKS_SOURCE),
    ]


def test_a_session_with_no_bars_splits_runs_of_one_source() -> None:
    plan = _plan(D1, D2, D3, D4)
    sources = {D1: TICKS_SOURCE, D2: TICKS_SOURCE, D3: None, D4: TICKS_SOURCE}
    returned = {D1, D4}  # D2 returned nothing; D3 was not read
    assert source_runs(plan, sources, returned) == [
        SourceRun(D1, D1, TICKS_SOURCE),
        SourceRun(D4, D4, TICKS_SOURCE),
    ]


def test_provider_served_sessions_leave_incomplete() -> None:
    incomplete = (
        Incomplete(D1, TickSessionStatus.MISSING),
        Incomplete(D2, TickSessionStatus.IN_FLIGHT),
        Incomplete(D3, TickSessionStatus.MISSING),
    )
    plan = _plan(D1, D2, D3, incomplete=incomplete)
    sources = {D1: BARS, D2: TICKS_SOURCE, D3: None}
    assert served_plan(plan, sources).incomplete == incomplete[1:]
