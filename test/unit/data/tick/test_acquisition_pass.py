"""How the pass's phases are wired (slice 224, LLD *The pass* data flow)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from manta_trading.data.tick.acquisition_pass import (
    DEFAULT_TIMING,
    AwaitTiming,
    pass_phases,
)
from manta_trading.data.tick.constants import (
    TICK_POLL_INTERVAL_SECONDS,
    TICK_WAIT_BUDGET_SECONDS,
)
from manta_trading.data.tick.pass_contract import TickPassPhaseName
from manta_trading.data.tick.provider import ITickFile


class _Reader:
    def open_file(self, path: Path) -> ITickFile:
        raise AssertionError("no file is opened while wiring")


def test_the_phases_are_the_five_in_execution_order() -> None:
    phases = pass_phases((None, None), False, _Reader())
    assert [phase.name for phase in phases] == list(TickPassPhaseName)
    assert [str(p.name) for p in phases] == [
        "reconcile",
        "availability",
        "purchase",
        "await",
        "definitions",
    ]


def test_each_run_gets_its_own_phases() -> None:
    first = pass_phases((None, None), False, _Reader())
    second: Any = pass_phases((None, None), False, _Reader())
    assert all(a is not b for a, b in zip(first, second, strict=True))


def test_the_shipped_timing_is_the_constants() -> None:
    assert DEFAULT_TIMING == AwaitTiming(
        TICK_WAIT_BUDGET_SECONDS, TICK_POLL_INTERVAL_SECONDS
    )
