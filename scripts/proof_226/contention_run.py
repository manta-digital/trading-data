"""The contention run's control flow and guards (LLD 226 TD7), dependency-free.

Every effect comes in through ``Probe`` (clock, sleep, samples, the Kalshi
timer and unit) and ``Load`` (the tick loop), so the guards are tested with
fakes. Two overlapped Kalshi firings with the tick loop running, then one
solo firing. Guards are named constants and one check function each.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NoReturn, Protocol

from proof_226.common import DATA_FREE_FLOOR_BYTES
from proof_226.contention_sampler import Sample

SAMPLE_SECONDS = 5.0
MEM_AVAILABLE_FLOOR = 16 * 1024**3
#: The Kalshi pass's longest run in the week before the slice (TD7).
KALSHI_WEEK_MAX_SECONDS = 324.0
KALSHI_TRIP_MULTIPLE = 2
KALSHI_TRIP_SECONDS = KALSHI_TRIP_MULTIPLE * KALSHI_WEEK_MAX_SECONDS
#: The Kalshi timer must fire within this long of each wait's start.
TIMER_HORIZON = timedelta(minutes=75)
#: The loop starts this long before a firing and stops this long after it.
LEAD_SECONDS = 120.0
TAIL_SECONDS = 60.0
OVERLAPPED_FIRINGS = 2
SOLO_FIRINGS = 1
#: A firing that has not started this long after its elapse time is a failure.
START_GRACE_SECONDS = 300.0


class Probe(Protocol):
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...
    def sample(self) -> Sample: ...
    def next_elapse(self) -> datetime | None: ...
    def kalshi_active(self) -> bool: ...


class Load(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


@dataclass(frozen=True)
class Firing:
    overlapped: bool
    started: datetime
    ended: datetime | None


@dataclass
class Outcome:
    samples: list[tuple[datetime, Sample, bool, bool]] = field(default_factory=list)
    """(time, sample, loop running, Kalshi active)."""
    firings: list[Firing] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    exit_code: int = 0
    kalshi_tripped: bool = False


class Stop(Exception):
    """A guard tripped; ``Outcome`` already records it."""


def host_trip(sample: Sample) -> str | None:
    """The memory and free-space guards; ``None`` when both hold."""
    if sample.mem_available < MEM_AVAILABLE_FLOOR:
        return (
            f"MemAvailable {sample.mem_available / 1024**3:.1f} GiB below "
            f"{MEM_AVAILABLE_FLOOR / 1024**3:.0f} GiB"
        )
    if sample.data_free < DATA_FREE_FLOOR_BYTES:
        return (
            f"/data free {sample.data_free / 1000**3:.0f} GB below "
            f"{DATA_FREE_FLOOR_BYTES / 1000**3:.0f} GB"
        )
    return None


def kalshi_trip(running_seconds: float) -> bool:
    return running_seconds > KALSHI_TRIP_SECONDS


class Run:
    def __init__(self, probe: Probe, load: Load) -> None:
        self.probe, self.load = probe, load
        self.out = Outcome()
        self.loaded = False
        self._kalshi_since: datetime | None = None

    def _tick(self) -> bool:
        """One sample and its guards; returns whether Kalshi is running."""
        sample, active = self.probe.sample(), self.probe.kalshi_active()
        now = self.probe.now()
        self.out.samples.append((now, sample, self.loaded, active))
        if (reason := host_trip(sample)) is not None:
            self._stop_load()
            self.out.lines.append(f"TRIP: {reason}; loop stopped")
            self.out.exit_code = 1
            raise Stop(reason)
        return active

    def _wait(self, seconds: float) -> None:
        end = self.probe.now() + timedelta(seconds=seconds)
        while self.probe.now() < end:
            self._tick()
            self.probe.sleep(SAMPLE_SECONDS)

    def _stop_load(self) -> None:
        if self.loaded:
            self.load.stop()
            self.loaded = False

    def _refuse(self, reason: str) -> NoReturn:
        self.out.lines.append(f"REFUSED: {reason}; no load started")
        self.out.exit_code = 1
        raise Stop(reason)

    def _firing(self, overlapped: bool) -> None:
        elapse = self.probe.next_elapse()
        now = self.probe.now()
        if elapse is None:
            self._refuse("the Kalshi timer has no next elapse (disabled?)")
        if elapse - now > TIMER_HORIZON:
            self._refuse(
                f"the Kalshi timer fires at {elapse:%H:%M:%S}, beyond "
                f"{TIMER_HORIZON.total_seconds() / 60:.0f} min"
            )
        self._wait(max((elapse - now).total_seconds() - LEAD_SECONDS, 0.0))
        if overlapped:
            self.load.start()
            self.loaded = True
        deadline = elapse + timedelta(seconds=START_GRACE_SECONDS)
        while not self._tick():
            if self.probe.now() > deadline:
                self._stop_load()
                self._refuse(f"the Kalshi pass did not start by {deadline:%H:%M:%S}")
            self.probe.sleep(SAMPLE_SECONDS)
        started = self.probe.now()
        while self._tick():
            running = (self.probe.now() - started).total_seconds()
            if overlapped and kalshi_trip(running):
                self._stop_load()
                self.out.kalshi_tripped = True
                self.out.lines.append(
                    f"TRIP: Kalshi exceeded {KALSHI_TRIP_MULTIPLE}× its weekly "
                    "maximum under tick load; "
                    "loop stopped, the pass left alone"
                )
                self.out.firings.append(Firing(overlapped, started, None))
                raise Stop("kalshi")
            self.probe.sleep(SAMPLE_SECONDS)
        self.out.firings.append(Firing(overlapped, started, self.probe.now()))
        self._wait(TAIL_SECONDS)
        self._stop_load()

    def run(self) -> Outcome:
        try:
            first = self.probe.sample()
            if (reason := host_trip(first)) is not None:
                self._refuse(f"first sample: {reason}")
            for overlapped in [True] * OVERLAPPED_FIRINGS + [False] * SOLO_FIRINGS:
                self._firing(overlapped)
        except Stop:
            pass  # recorded in self.out by whichever guard raised it
        finally:
            self._stop_load()
        return self.out
