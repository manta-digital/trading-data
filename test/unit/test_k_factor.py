"""Unit tests for ``manta_trading.data.adjustment.k_factor``.

Coverage:
  - Empty inputs (no corporate actions => k = 1).
  - Pure split (target before split => ratio_from / ratio_to).
  - Multiple splits compose multiplicatively.
  - Pure dividend ((prev_close - amount) / prev_close).
  - Split + dividend in the same window compose correctly.
  - Target date AFTER all actions => k = 1.
  - Symbol filtering (other-symbol actions ignored).
  - Boundary case: action.ex_date == target_date is excluded (strictly greater).
  - AAPL 2020-08-31 4:1 regression — verifies the formula matches EODHD's
    documented "ratio_from / ratio_to" and the probe-derived split-only k.
  - Validation: missing prev_close raises KeyError; non-positive ratios or
    closes raise ValueError.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from manta_trading.data.adjustment import Dividend, Split
from manta_trading.data.adjustment.k_factor import (
    CaSnapshot,
    compute_k_factor,
    compute_snapshot_id,
)

SYM = "AAPL"


def _make_snapshot(
    splits: list[Split],
    dividends: list[Dividend],
    prev_closes: dict,
) -> CaSnapshot:
    sid = compute_snapshot_id(splits, dividends)
    return CaSnapshot(
        symbol=SYM,
        splits=tuple(splits),
        dividends=tuple(dividends),
        prev_closes=prev_closes,
        snapshot_id=sid,
    )


# ---------------------------------------------------------------------------
# Empty / no-op cases
# ---------------------------------------------------------------------------


def test_no_actions_returns_one() -> None:
    assert compute_k_factor(SYM, date(2020, 1, 1), [], [], {}) == Decimal("1")


def test_target_after_all_actions_returns_one() -> None:
    """Actions strictly before target_date have no effect."""
    splits = [Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))]
    divs = [Dividend(SYM, date(2020, 11, 6), Decimal("0.205"))]
    prev_closes = {date(2020, 11, 6): Decimal("100")}
    assert compute_k_factor(SYM, date(2025, 1, 1), splits, divs, prev_closes) == Decimal("1")


def test_action_on_exact_target_date_is_excluded() -> None:
    """Strictly greater: ex_date == target_date does not contribute."""
    splits = [Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))]
    assert compute_k_factor(SYM, date(2020, 8, 31), splits, [], {}) == Decimal("1")


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------


def test_pure_split_2_for_1() -> None:
    splits = [Split(SYM, date(2020, 6, 1), Decimal("2"), Decimal("1"))]
    assert compute_k_factor(SYM, date(2020, 5, 1), splits, [], {}) == Decimal("0.5")


def test_pure_split_4_for_1_aapl_2020_08_31() -> None:
    """AAPL 2020-08-31 4:1 split. Target before => k = 1/4 = 0.25 exact."""
    splits = [Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))]
    assert compute_k_factor(SYM, date(2020, 8, 25), splits, [], {}) == Decimal("0.25")


def test_multiple_splits_compose() -> None:
    """2-for-1 followed by 4-for-1 => k = 0.5 * 0.25 = 0.125."""
    splits = [
        Split(SYM, date(2020, 6, 1), Decimal("2"), Decimal("1")),
        Split(SYM, date(2020, 12, 1), Decimal("4"), Decimal("1")),
    ]
    assert compute_k_factor(SYM, date(2020, 1, 1), splits, [], {}) == Decimal("0.125")


def test_split_with_unusual_ratio() -> None:
    """7-for-1 split (AAPL 2014-06-09) => k = 1/7."""
    splits = [Split(SYM, date(2014, 6, 9), Decimal("7"), Decimal("1"))]
    expected = Decimal("1") / Decimal("7")
    assert compute_k_factor(SYM, date(2014, 6, 8), splits, [], {}) == expected


# ---------------------------------------------------------------------------
# Dividends
# ---------------------------------------------------------------------------


def test_pure_dividend() -> None:
    """$1 dividend on $100 prev_close => k = 0.99."""
    divs = [Dividend(SYM, date(2020, 6, 1), Decimal("1"))]
    prev_closes = {date(2020, 6, 1): Decimal("100")}
    assert compute_k_factor(SYM, date(2020, 5, 1), [], divs, prev_closes) == Decimal("0.99")


def test_dividend_uses_strictly_greater_filter() -> None:
    """Dividend on the target_date itself does not apply."""
    divs = [Dividend(SYM, date(2020, 6, 1), Decimal("1"))]
    prev_closes = {date(2020, 6, 1): Decimal("100")}
    assert compute_k_factor(SYM, date(2020, 6, 1), [], divs, prev_closes) == Decimal("1")


def test_missing_prev_close_raises_key_error() -> None:
    divs = [Dividend(SYM, date(2020, 6, 1), Decimal("1"))]
    with pytest.raises(KeyError, match="prev_close missing"):
        compute_k_factor(SYM, date(2020, 5, 1), [], divs, {})


# ---------------------------------------------------------------------------
# Composite split + dividend
# ---------------------------------------------------------------------------


def test_split_and_dividend_compose() -> None:
    """Split (4:1) followed by dividend ($1 on $100): k = 0.25 * 0.99 = 0.2475."""
    splits = [Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))]
    divs = [Dividend(SYM, date(2020, 11, 6), Decimal("1"))]
    prev_closes = {date(2020, 11, 6): Decimal("100")}
    expected = Decimal("0.25") * Decimal("0.99")
    assert compute_k_factor(SYM, date(2020, 8, 25), splits, divs, prev_closes) == expected


def test_dividend_post_split_on_low_price() -> None:
    """Realistic post-split AAPL: $0.205 dividend on a ~$108 prev_close
    contributes ~0.99810 to k. Combined with the 4:1 split that would
    produce a pre-split k of 0.25 * 0.99810 = 0.2495."""
    splits = [Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))]
    divs = [Dividend(SYM, date(2020, 11, 6), Decimal("0.205"))]
    prev_closes = {date(2020, 11, 6): Decimal("108")}
    pre = Decimal("0.25") * (Decimal("108") - Decimal("0.205")) / Decimal("108")
    actual = compute_k_factor(SYM, date(2020, 8, 25), splits, divs, prev_closes)
    assert actual == pre


# ---------------------------------------------------------------------------
# Symbol filtering
# ---------------------------------------------------------------------------


def test_other_symbol_actions_ignored() -> None:
    splits = [
        Split("MSFT", date(2020, 6, 1), Decimal("2"), Decimal("1")),
        Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1")),
    ]
    divs = [
        Dividend("MSFT", date(2020, 7, 1), Decimal("0.50")),
    ]
    # Only AAPL split should apply for AAPL target.
    assert compute_k_factor(SYM, date(2020, 5, 1), splits, divs, {}) == Decimal("0.25")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_zero_split_ratio_raises() -> None:
    splits = [Split(SYM, date(2020, 6, 1), Decimal("0"), Decimal("1"))]
    with pytest.raises(ValueError, match="non-positive ratio"):
        compute_k_factor(SYM, date(2020, 5, 1), splits, [], {})


def test_negative_prev_close_raises() -> None:
    divs = [Dividend(SYM, date(2020, 6, 1), Decimal("1"))]
    prev_closes = {date(2020, 6, 1): Decimal("-5")}
    with pytest.raises(ValueError, match="non-positive"):
        compute_k_factor(SYM, date(2020, 5, 1), [], divs, prev_closes)


# ---------------------------------------------------------------------------
# Probe-derived sanity check (decimal precision over many actions)
# ---------------------------------------------------------------------------


def test_aapl_split_with_many_dividends_decimal_stability() -> None:
    """Compose a 4:1 split with eight $0.22 dividends on $150 prev_closes
    and assert that the result is stable bit-exact under Decimal arithmetic.

    This mirrors the structure of a real adjustment chain (one big split,
    a string of small dividend factors) without depending on EODHD's
    actual published k for a specific date — that is verified by the
    ``verify-adjustment`` CLI in slice 127 task 26.
    """
    splits = [Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))]
    divs = [
        Dividend(SYM, date(2021, 2, 5) + _shift(i), Decimal("0.22"))
        for i in range(8)
    ]
    prev_closes = {d.ex_date: Decimal("150") for d in divs}

    # Recompute the expected value by hand.
    expected = Decimal("0.25")
    for _ in range(8):
        expected *= (Decimal("150") - Decimal("0.22")) / Decimal("150")

    actual = compute_k_factor(SYM, date(2020, 8, 25), splits, divs, prev_closes)
    assert actual == expected


# ---------------------------------------------------------------------------
# ca_snapshot overload round-trip equivalence (T7)
# ---------------------------------------------------------------------------


def test_ca_snapshot_overload_matches_positional() -> None:
    """compute_k_factor with ca_snapshot returns the same Decimal as positional form."""
    splits = [Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))]
    divs = [Dividend(SYM, date(2020, 11, 6), Decimal("1"))]
    prev_closes = {date(2020, 11, 6): Decimal("100")}
    target = date(2020, 8, 25)

    positional = compute_k_factor(SYM, target, splits, divs, prev_closes)
    snap = _make_snapshot(splits, divs, prev_closes)
    via_snapshot = compute_k_factor(SYM, target, ca_snapshot=snap)

    assert positional == via_snapshot


def test_ca_snapshot_overload_no_actions() -> None:
    snap = _make_snapshot([], [], {})
    assert compute_k_factor(SYM, date(2020, 1, 1), ca_snapshot=snap) == Decimal("1")


def test_ca_snapshot_overload_split_only() -> None:
    splits = [Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))]
    snap = _make_snapshot(splits, [], {})
    assert compute_k_factor(SYM, date(2020, 8, 25), ca_snapshot=snap) == Decimal("0.25")


def test_ca_snapshot_overload_dividend_only() -> None:
    divs = [Dividend(SYM, date(2020, 6, 1), Decimal("1"))]
    prev_closes = {date(2020, 6, 1): Decimal("100")}
    snap = _make_snapshot([], divs, prev_closes)
    assert compute_k_factor(SYM, date(2020, 5, 1), ca_snapshot=snap) == Decimal("0.99")



def _shift(i: int) -> "object":
    """Return a timedelta of i*90 days (avoid duplicate ex_dates in the
    synthetic test). Importing here to keep the typing imports flat."""
    from datetime import timedelta

    return timedelta(days=90 * i)
