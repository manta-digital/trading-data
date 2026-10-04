"""The contention run's guards on fake samples and a fake clock (226 TD7).

No database, no timer, no load: ``FakeProbe`` scripts the Kalshi firings and
the host readings, ``FakeLoad`` records starts and stops.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from proof_226 import contention  # noqa: E402
from proof_226 import contention_run as cr  # noqa: E402
from proof_226.common import ProofUrls  # noqa: E402
from proof_226.contention_report import verdict  # noqa: E402
from proof_226.contention_sampler import Sample  # noqa: E402

T0 = datetime(2026, 10, 3, 20, 0, tzinfo=UTC)
HEALTHY = Sample(
    at=0.0,
    cpu_busy_pct=10.0,
    iowait_pct=1.0,
    mem_available=60 * 1024**3,
    committed_as=1,
    commit_limit=2,
    data_free=1000 * 1000**3,
    disk_read_bps={},
    disk_write_bps={},
    prod_commits_ps=1.0,
    prod_tuples_ps=1.0,
    tick_rows_ps=0.0,
)


@dataclass
class FakeProbe:
    """Kalshi fires at ``firings[i]`` and runs ``run_seconds``; the clock moves
    only on ``sleep``. ``sample_at`` overrides the reading from a time on."""

    firings: list[datetime]
    run_seconds: float = 200.0
    sample_at: tuple[datetime, Sample] | None = None
    clock: datetime = T0
    samples_taken: int = 0

    def now(self) -> datetime:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += timedelta(seconds=seconds)

    def sample(self) -> Sample:
        self.samples_taken += 1
        if self.sample_at is not None and self.clock >= self.sample_at[0]:
            return self.sample_at[1]
        return HEALTHY

    def next_elapse(self) -> datetime | None:
        upcoming = [f for f in self.firings if f > self.clock]
        return upcoming[0] if upcoming else None

    def kalshi_active(self) -> bool:
        return any(
            f <= self.clock < f + timedelta(seconds=self.run_seconds)
            for f in self.firings
        )


@dataclass
class FakeLoad:
    events: list[str] = field(default_factory=list)

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


def _hourly(count: int = 3, first_in: float = 10) -> list[datetime]:
    first = T0 + timedelta(minutes=first_in)
    return [first + timedelta(hours=n) for n in range(count)]


def test_a_clean_run_overlaps_two_firings_then_one_solo() -> None:
    load = FakeLoad()
    out = cr.Run(FakeProbe(_hourly()), load).run()
    assert out.exit_code == 0 and out.lines == []
    assert [f.overlapped for f in out.firings] == [True, True, False]
    assert load.events == ["start", "stop", "start", "stop"]
    solo = [s for _, s, loaded, active in out.samples if active and not loaded]
    assert solo, "the third firing is sampled with no load"


def test_a_first_sample_below_the_memory_floor_starts_nothing() -> None:
    low = replace(HEALTHY, mem_available=cr.MEM_AVAILABLE_FLOOR - 1)
    load = FakeLoad()
    out = cr.Run(FakeProbe(_hourly(), sample_at=(T0, low)), load).run()
    assert out.exit_code == 1
    assert load.events == []
    assert out.lines[0].startswith("REFUSED: first sample: MemAvailable")


def test_memory_below_the_floor_under_load_stops_the_loop() -> None:
    firings = _hourly()
    low = replace(HEALTHY, mem_available=cr.MEM_AVAILABLE_FLOOR - 1)
    trip_at = firings[0] + timedelta(seconds=30)
    load = FakeLoad()
    out = cr.Run(FakeProbe(firings, sample_at=(trip_at, low)), load).run()
    assert out.exit_code == 1
    assert load.events == ["start", "stop"]
    assert out.lines == [
        f"TRIP: MemAvailable {(cr.MEM_AVAILABLE_FLOOR - 1) / 1024**3:.1f} GiB "
        "below 16 GiB; loop stopped"
    ]


def test_data_below_the_floor_stops_the_loop() -> None:
    firings = _hourly()
    full = replace(HEALTHY, data_free=cr.DATA_FREE_FLOOR_BYTES - 1)
    load = FakeLoad()
    out = cr.Run(FakeProbe(firings, sample_at=(firings[0], full)), load).run()
    assert out.exit_code == 1
    assert load.events == ["start", "stop"]
    assert out.lines[0].startswith("TRIP: /data free")


def test_kalshi_past_twice_its_weekly_max_stops_the_loop_only() -> None:
    load = FakeLoad()
    probe = FakeProbe(_hourly(), run_seconds=cr.KALSHI_TRIP_SECONDS + 60)
    out = cr.Run(probe, load).run()
    assert out.kalshi_tripped and out.exit_code == 0
    assert load.events == ["start", "stop"]
    assert out.lines[0].startswith("TRIP: Kalshi exceeded 2×")
    assert out.firings[0].ended is None


def test_a_timer_beyond_75_minutes_starts_nothing() -> None:
    load = FakeLoad()
    out = cr.Run(FakeProbe(_hourly(first_in=76)), load).run()
    assert out.exit_code == 1 and load.events == []
    assert "beyond 75 min" in out.lines[0]  # from TIMER_HORIZON


def test_a_disabled_timer_starts_nothing() -> None:
    load = FakeLoad()
    out = cr.Run(FakeProbe([]), load).run()
    assert out.exit_code == 1 and load.events == []
    assert "no next elapse" in out.lines[0]


def test_an_error_mid_run_still_stops_the_loop() -> None:
    class Boom(FakeProbe):
        def kalshi_active(self) -> bool:
            if self.clock >= self.firings[0]:
                raise RuntimeError("probe failed")
            return False

    load = FakeLoad()
    with pytest.raises(RuntimeError, match="probe failed"):
        cr.Run(Boom(_hourly()), load).run()
    assert load.events == ["start", "stop"]


def test_verdict_names_contention_above_the_weekly_max() -> None:
    assert verdict([300.0, 330.0], []).startswith("measurable contention")
    calm = verdict([200.0, 310.0], [], workers=2)
    assert calm.startswith("none measured at 2 ingest workers")
    assert "not a clearance for the minute pass" in calm


def test_a_failed_tick_loop_gives_no_verdict() -> None:
    """Calm durations are no evidence when the load was not running."""
    out = verdict([200.0, 210.0], ["ingest exited 1: boom"])
    assert out.startswith("none: the tick loop failed 1 time(s)")


def _loop(monkeypatch: pytest.MonkeyPatch, mt: str) -> contention.TickLoop:
    monkeypatch.setattr(contention, "MT", Path(mt))
    return contention.TickLoop(ProofUrls("postgresql://x/p", "postgresql://x/m"))


def test_a_failing_ingest_is_recorded_not_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resets: list[ProofUrls] = []

    def reset(urls: ProofUrls) -> None:
        if len(resets) == 2:
            loop._stop.set()  # pyright: ignore[reportPrivateUsage]
        resets.append(urls)

    monkeypatch.setattr(contention, "reset", reset)
    loop = _loop(monkeypatch, "/bin/false")
    loop.start()
    assert loop._thread is not None  # pyright: ignore[reportPrivateUsage]
    loop._thread.join(timeout=10)  # pyright: ignore[reportPrivateUsage]
    loop.stop()
    assert loop.iterations == 0
    assert loop.failures == ["ingest exited 1: no stderr"] * 2


def test_a_dying_loop_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    def reset(_: ProofUrls) -> None:
        raise RuntimeError("reset failed")

    monkeypatch.setattr(contention, "reset", reset)
    loop = _loop(monkeypatch, "/bin/true")
    loop.start()
    loop.stop()
    assert loop.failures == ["tick loop died: RuntimeError('reset failed')"]


def test_a_stop_during_reset_starts_no_ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race: stop lands while reset runs; no ingest may start after it."""
    started = threading.Event()
    release = threading.Event()

    def reset(_: ProofUrls) -> None:
        started.set()
        release.wait()

    monkeypatch.setattr(contention, "reset", reset)
    loop = _loop(monkeypatch, "/bin/true")
    loop.start()
    started.wait()
    stopper = threading.Thread(target=loop.stop)
    stopper.start()
    while not loop._stop.is_set():  # pyright: ignore[reportPrivateUsage]
        pass
    release.set()
    stopper.join(timeout=10)
    assert not stopper.is_alive()
    assert loop._proc is None  # pyright: ignore[reportPrivateUsage]
