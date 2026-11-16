"""Slice 309: which part of a session ``scan_params`` scans for ``[from, until)``.

A bar is selected by its start and kept whole, so ``from`` rounds up to the next
bar start and ``until`` keeps the bar that starts before it, ending with that bar.
"""

from __future__ import annotations

from datetime import date

import pytest

from manta_trading.data.tick.bar_reads import scan_params
from manta_trading.data.tick.tick_reads import TickWindow
from manta_trading.data.tick.time_bar_size import BAR_SIZE_UNITS_NS, parse_bar_size

S = BAR_SIZE_UNITS_NS["s"]
OPEN = 1_000_000 * S
HOURS_23 = 23 * BAR_SIZE_UNITS_NS["h"]
WINDOW = TickWindow(date(2024, 12, 2), 1, "ESZ4", OPEN, OPEN + HOURS_23 - 1)


def _scan(size: str, from_s: int | None, until_s: int | None) -> tuple[int, int] | None:
    """The scanned ``(open, close)`` offsets from the session open, in seconds,
    for bounds given in seconds from the open."""
    bounds = tuple(None if s is None else OPEN + s * S for s in (from_s, until_s))
    params = scan_params(WINDOW, parse_bar_size(size).width_ns, bounds)  # type: ignore[arg-type]
    if params is None:
        return None
    return (params["open_ns"] - OPEN) // S, (params["close_ns"] + 1 - OPEN) // S


@pytest.mark.parametrize(
    ("size", "from_s", "until_s", "expected"),
    [
        # from rounds up to the next bar start; a bar that began earlier is out.
        ("1m", 90, None, (120, 82_800)),
        ("1m", 120, None, (120, 82_800)),
        ("5m", 301, None, (600, 82_800)),
        ("90m", 1, None, (5_400, 82_800)),
        # until keeps the bar that starts before it, whole.
        ("1m", None, 121, (0, 180)),
        ("1m", None, 120, (0, 120)),
        ("5m", None, 301, (0, 600)),
        ("90m", None, 5_401, (0, 10_800)),
        # both ends.
        ("5m", 301, 901, (600, 1_200)),
        # before the open: the whole session side is kept.
        ("5m", -600, None, (0, 82_800)),
    ],
)
def test_unaligned_bounds_keep_whole_bars(
    size: str, from_s: int | None, until_s: int | None, expected: tuple[int, int]
) -> None:
    assert _scan(size, from_s, until_s) == expected


@pytest.mark.parametrize(
    ("from_s", "until_s", "expected"),
    [
        (None, None, (0, 82_800)),
        (0, None, (0, 82_800)),
        (1, None, None),  # the one bar starts before ``from``, even though it covers it
        (None, 1, (0, 82_800)),  # it starts before ``until``, so it is whole
        (None, 0, None),
    ],
)
def test_the_session_day_is_one_bar_selected_by_its_start(
    from_s: int | None, until_s: int | None, expected: tuple[int, int] | None
) -> None:
    assert _scan("1d", from_s, until_s) == expected


@pytest.mark.parametrize(
    ("from_s", "until_s"),
    [(None, 0), (None, -60), (82_800, None), (120, 120), (301, 300)],
)
def test_no_bar_starts_inside_the_bounds(
    from_s: int | None, until_s: int | None
) -> None:
    assert _scan("1m", from_s, until_s) is None
