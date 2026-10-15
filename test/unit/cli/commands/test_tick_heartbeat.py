"""The throttled ingest heartbeat (slice 234, Task 3.5)."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from manta_trading.cli.commands.tick_heartbeat import IngestHeartbeat, expected_rows


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class _Unit:
    provider_record_count: int | None


def _heartbeat(
    clock: FakeClock,
) -> tuple[IngestHeartbeat, list[tuple[int, int | None]]]:
    calls: list[tuple[int, int | None]] = []
    beat = IngestHeartbeat(
        lambda done, total: calls.append((done, total)),
        interval_seconds=30,
        clock=clock,
    )
    return beat, calls


def test_rapid_batches_report_once_per_interval() -> None:
    clock = FakeClock()
    beat, calls = _heartbeat(clock)
    beat.set_units([_Unit(500)])  # type: ignore[list-item]
    for _ in range(10):
        beat.add(10)
    assert calls == []
    clock.now += 30
    for _ in range(10):
        beat.add(10)
    assert calls == [(110, 500)]
    clock.now += 29
    beat.add(5)
    assert len(calls) == 1
    clock.now += 1
    beat.add(5)
    assert calls[-1] == (210, 500)


def test_total_is_none_when_any_unit_lacks_a_count() -> None:
    units = [_Unit(5), _Unit(None)]
    assert expected_rows(units) is None  # type: ignore[arg-type]
    assert expected_rows([_Unit(5), _Unit(7)]) == 12  # type: ignore[list-item]
    assert expected_rows([]) == 0
    clock = FakeClock()
    beat, calls = _heartbeat(clock)
    beat.set_units(units)  # type: ignore[arg-type]
    beat.add(3)
    beat.flush()
    assert calls == [(3, None)]


def test_flush_reports_exactly_once() -> None:
    clock = FakeClock()
    beat, calls = _heartbeat(clock)
    beat.add(7)
    beat.flush()
    beat.flush()
    assert calls == [(7, None)]


def test_concurrent_callers_lose_no_counts() -> None:
    clock = FakeClock()
    beat, calls = _heartbeat(clock)

    def work() -> None:
        for _ in range(10_000):
            beat.add(1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    beat.flush()
    assert calls == [(80_000, None)]
