"""Simulation harness for the daemon runner behavior tests (slice 912 Task 5).

The Task 5 tests drive the *real* ``Runner`` loop across simulated time. Two
pieces make that possible without a database or a provider:

- :class:`AdvancingClock` — the runner's only source of time, moved forward by
  the injected ``sleep``, so a loop that would block for fifteen minutes in
  production completes instantly while still exercising the real cadence
  arithmetic in ``daily_cycle_due`` and ``sleep_until_next_due_event``.
- :class:`FakeAcquisitionState` — an in-memory stand-in for the rows
  ``_PENDING_DAILY_SYMBOLS_SQL`` returns, so ``pending_daily_symbols`` itself
  runs for real against state the test stamps exactly as ``update_data_gaps``
  does in production.

What is faked is the I/O boundary only: the SQL round trip and the provider
call. The work-list derivation, the cadence gates, and the loop's idle-reason
handling are all the shipping implementations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, auto
from typing import Any
from unittest.mock import MagicMock

from manta_trading.data.acquisition.daemon.daily import (
    CycleReport,
    daily_pass_boundary,
    pending_daily_symbols,
)


class SimulationEnded(Exception):
    """Raised by the injected sleep when simulated time runs past the horizon.

    Bounds a ``--forever`` loop, which by contract never exits on its own. The
    test asserting on such a loop expects this to escape ``Runner.start()``;
    anything else means the loop stopped for a reason the test did not intend.
    """


class AdvancingClock:
    """A clock the test moves forward explicitly.

    ``sleep`` is what advances it, which keeps the simulation honest: the loop
    can only reach a later instant by actually deciding to wait for one.
    """

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def sleep_until(self, horizon: datetime) -> Callable[[float], None]:
        """Return a ``sleep`` that advances this clock, ending at ``horizon``.

        A zero-length sleep would not advance the clock, so a loop that always
        has something due never reaches the horizon; that is a real hang and is
        left to the suite timeout rather than papered over here.
        """

        def _sleep(seconds: float) -> None:
            self.advance(seconds)
            if self.now >= horizon:
                raise SimulationEnded(f"simulated past {horizon:%H:%M} UTC")

        return _sleep


class FakeAcquisitionState:
    """In-memory stand-in for the state the daily work-list query reads.

    Holds exactly the two facts the query derives per symbol — whether its
    calendar resolves to any trading session, and when it was last attempted —
    and serves them in the caller's scope order, matching the real statement's
    ``ORDER BY s.ord``.
    """

    def __init__(
        self,
        *,
        no_calendar: Iterable[str] = (),
        unknown: Iterable[str] = (),
    ) -> None:
        self._attempts: dict[str, datetime] = {}
        self._no_calendar = frozenset(no_calendar)
        self._unknown = frozenset(unknown)

    def stamp(self, symbol: str, when: datetime) -> None:
        """Record an attempt, as ``update_data_gaps`` does after a fetch."""
        self._attempts[symbol] = when

    def stamp_all(self, symbols: Iterable[str], when: datetime) -> None:
        for symbol in symbols:
            self.stamp(symbol, when)

    def attempted(self) -> set[str]:
        return set(self._attempts)

    def connection(self) -> MagicMock:
        """A connection answering the work-list query against this state."""
        captured: dict[str, list[tuple[str, bool, bool, datetime | None]]] = {}

        def _execute(_sql: str, params: dict[str, Any]) -> None:
            captured["rows"] = [
                (
                    symbol,
                    symbol not in self._unknown,
                    symbol not in self._no_calendar,
                    self._attempts.get(symbol),
                )
                for symbol in params["symbols"]
            ]

        cur = MagicMock()
        cur.execute.side_effect = _execute
        cur.fetchall.side_effect = lambda: captured["rows"]
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn


class Interrupt(StrEnum):
    """How a simulated pass stops before reaching every pending symbol."""

    NONE = auto()

    RAISE = auto()
    """Models a crash mid-pass: the runner catches it and keeps looping."""

    SIGNAL = auto()
    """Models SIGTERM mid-pass: ``should_continue`` flips and the loop exits."""


@dataclass
class RecordingDailyCycle:
    """A daily cycle that derives its work list for real, then records it.

    Stands in for ``run_daily_cycle`` at the provider boundary while keeping the
    part under test — deriving pending work from durable per-symbol state and
    reporting an empty derivation as ``nothing_actionable`` — genuine. The
    ordering mirrors ``daily.py``: an empty work list returns before anything
    that would cost a provider call.
    """

    store: FakeAcquisitionState
    clock: Callable[[], datetime]
    stop_after_symbols: int | None = None
    interrupt: Interrupt = Interrupt.NONE
    runner: Any = None
    """Set by the test after construction; only the SIGNAL interrupt needs it."""

    pending_seen: list[list[str]] = field(default_factory=list)
    unactionable_seen: list[int] = field(default_factory=list)
    ran_at: list[datetime] = field(default_factory=list)
    provider_calls: int = 0

    def __call__(
        self,
        *,
        symbols: list[str] | None = None,
        should_continue: Callable[[], bool] | None = None,
        on_symbol: Callable[..., None] | None = None,
    ) -> CycleReport:
        self.ran_at.append(self.clock())
        scope = list(symbols or [])
        work = pending_daily_symbols(
            self.store.connection(), scope, daily_pass_boundary(self.clock())
        )
        self.pending_seen.append(list(work.pending))
        self.unactionable_seen.append(len(work.unactionable))

        report = CycleReport(
            unactionable_no_calendar=len(work.unactionable_no_calendar),
            unknown_symbols=len(work.unknown_symbols),
        )
        if not work.pending:
            report.nothing_actionable = True
            return report

        self.provider_calls += 1
        for symbol in work.pending:
            if should_continue is not None and not should_continue():
                break
            self.store.stamp(symbol, self.clock())
            report.success_count += 1
            if (
                self.stop_after_symbols is not None
                and report.success_count >= self.stop_after_symbols
            ):
                self._interrupt()
        return report

    def _interrupt(self) -> None:
        if self.interrupt is Interrupt.RAISE:
            raise RuntimeError("simulated crash mid-pass")
        if self.interrupt is Interrupt.SIGNAL:
            # Flip the flag the real SIGTERM handler flips. The cycle's own
            # `should_continue()` poll on the next symbol is what actually stops
            # the pass, exactly as in production.
            self.runner._should_exit = True
