"""``futures_read``: ``clip`` on closed windows (231 TD3) and the ``admit``
hook of ``read_series`` (308 D5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import pytest

from manta_trading.api_server.futures_read import clip, read_series
from manta_trading.data.tick.get_plan import ContractTarget, GetPlan
from manta_trading.data.tick.tick_reads import OnBatch, TickWindow

WINDOW = TickWindow(date(2024, 9, 18), 1, "ESZ4", open_ns=100, close_ns=200)
LATER = TickWindow(date(2024, 9, 19), 1, "ESZ4", open_ns=300, close_ns=400)


def _span(windows: tuple[TickWindow, ...]) -> list[tuple[int, int]]:
    return [(w.open_ns, w.close_ns) for w in windows]


def test_no_bounds_keep_every_window() -> None:
    assert clip([WINDOW, LATER], (None, None)) == (WINDOW, LATER)


def test_a_from_inside_a_window_moves_its_open() -> None:
    assert _span(clip([WINDOW, LATER], (150, None))) == [(150, 200), (300, 400)]


def test_until_is_exclusive() -> None:
    assert _span(clip([WINDOW], (None, 180))) == [(100, 179)]


def test_an_until_at_a_window_open_drops_it() -> None:
    assert _span(clip([WINDOW, LATER], (None, 300))) == [(100, 200)]


def test_a_window_outside_the_bounds_is_dropped() -> None:
    assert clip([WINDOW], (250, 280)) == ()


def test_adjacent_bounds_partition_a_window_without_overlap() -> None:
    first = _span(clip([WINDOW], (None, 150)))
    second = _span(clip([WINDOW], (150, None)))

    assert first == [(100, 149)] and second == [(150, 200)]


def test_the_window_keeps_its_session_and_contract() -> None:
    (clipped,) = clip([WINDOW], (150, 160))

    assert (clipped.session_date, clipped.raw_symbol) == (date(2024, 9, 18), "ESZ4")


# -- the admit hook (308 D5) ---------------------------------------------------

PLAN = GetPlan(
    ContractTarget("ESZ4", 1, "ES"), None, None, (WINDOW, LATER), (), (), (), None
)


class _Conn:
    """Just what ``read_series`` calls on its connection."""

    async def set_isolation_level(self, level: Any) -> None:
        pass

    async def set_read_only(self, value: bool) -> None:
        pass

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield


def _recorder(events: list[str]) -> tuple[Any, Any]:
    async def plan_for(conn: Any) -> GetPlan:
        events.append("plan")
        return PLAN

    async def read(conn: Any, window: TickWindow, on_batch: OnBatch) -> int:
        events.append(f"read {window.open_ns}")
        on_batch([(window.open_ns,)])
        return 1

    return plan_for, read


async def test_admit_runs_once_after_the_plan_and_before_any_window() -> None:
    events: list[str] = []
    plan_for, read = _recorder(events)
    seen: list[GetPlan] = []

    async def admit(conn: Any, plan: GetPlan) -> None:
        events.append("admit")
        seen.append(plan)

    conn: Any = _Conn()
    await read_series(conn, plan_for, read, 10, admit=admit)

    assert events == ["plan", "admit", "read 100", "read 300"]
    assert seen == [PLAN]


async def test_no_admit_reads_as_before() -> None:
    events: list[str] = []
    plan_for, read = _recorder(events)

    conn: Any = _Conn()
    plan, rows = await read_series(conn, plan_for, read, 10)

    assert events == ["plan", "read 100", "read 300"]
    assert (plan, rows) == (PLAN, [(100,), (300,)])


async def test_an_admit_error_reads_no_window() -> None:
    events: list[str] = []
    plan_for, read = _recorder(events)

    async def admit(conn: Any, plan: GetPlan) -> None:
        raise LookupError("refused")

    conn: Any = _Conn()
    with pytest.raises(LookupError, match="refused"):
        await read_series(conn, plan_for, read, 10, admit=admit)

    assert events == ["plan"]
