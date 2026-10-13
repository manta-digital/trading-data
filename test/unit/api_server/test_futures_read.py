"""``futures_read.clip``: half-open instant bounds on closed windows (231 TD3)."""

from __future__ import annotations

from datetime import date

from manta_trading.api_server.futures_read import clip
from manta_trading.data.tick.tick_reads import TickWindow

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
