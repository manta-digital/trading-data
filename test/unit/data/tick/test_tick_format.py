"""``price_text`` and ``ts_text`` (slice 230, TD4, FR9)."""

from __future__ import annotations

import pytest

from manta_trading.data.tick.constants import TICK_PRICE_SCALE, UNDEF_INT64
from manta_trading.data.tick.tick_format import price_text, ts_text


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        (5_950_250_000_000, "5950.25"),  # a quarter tick
        (5_950_000_000_000, "5950"),  # a whole number, never 5.95E+3
        (-500_000_000, "-0.5"),  # spreads can trade negative
        (1, "0.000000001"),  # the smallest step, never 1E-9
        (123_456_789, "0.123456789"),
        (0, "0"),
        (UNDEF_INT64 - 1, "9223372036.854775806"),  # exact at the top end
    ],
)
def test_price_text_is_exact(raw: int, text: str) -> None:
    assert price_text(raw) == text


def test_undefined_price_is_none_not_the_sentinel() -> None:
    assert price_text(UNDEF_INT64) is None


def test_scale_is_dbn_fixed_price_scale() -> None:
    assert TICK_PRICE_SCALE == 1_000_000_000


@pytest.mark.parametrize(
    ("ns", "text"),
    [
        (0, "1970-01-01T00:00:00.000000000Z"),
        (1_726_493_400_000_000_000, "2024-09-16T13:30:00.000000000Z"),
        (1_726_493_400_000_000_007, "2024-09-16T13:30:00.000000007Z"),
        (1_726_493_400_123_456_789, "2024-09-16T13:30:00.123456789Z"),
    ],
)
def test_ts_text_has_nine_fraction_digits(ns: int, text: str) -> None:
    assert ts_text(ns) == text
