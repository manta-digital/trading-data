"""Unit tests for CaSnapshot and compute_snapshot_id (slice 143).

Coverage:
  - CaSnapshot.frozen: assigning to any field raises FrozenInstanceError.
  - CaSnapshot.not_hashable: hash() raises TypeError (dict field blocks it).
  - compute_snapshot_id ordering invariant: different iteration order of
    inputs produces an identical digest.
  - compute_snapshot_id stable across processes: a subprocess invocation
    with the same fixture produces the same hex string.
  - compute_snapshot_id ignores fetched_at: canary test confirming the
    function only reads the fields it canonicalizes (ex_date, ratio/amount).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from manta_trading.data.adjustment.k_factor import (
    CaSnapshot,
    Dividend,
    Split,
    compute_snapshot_id,
)

SYM = "AAPL"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SPLIT_A = Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))
_SPLIT_B = Split(SYM, date(2014, 6, 9), Decimal("7"), Decimal("1"))
_DIV_A = Dividend(SYM, date(2021, 2, 5), Decimal("0.22"))
_DIV_B = Dividend(SYM, date(2021, 5, 7), Decimal("0.22"))


def _make_snapshot(
    splits: tuple[Split, ...] = (_SPLIT_A,),
    dividends: tuple[Dividend, ...] = (_DIV_A,),
) -> CaSnapshot:
    sid = compute_snapshot_id(splits, dividends)
    return CaSnapshot(
        symbol=SYM,
        splits=splits,
        dividends=dividends,
        prev_closes={date(2021, 2, 5): Decimal("134")},
        snapshot_id=sid,
    )


# ---------------------------------------------------------------------------
# CaSnapshot: frozen behaviour
# ---------------------------------------------------------------------------


def test_casnapshot_frozen() -> None:
    snap = _make_snapshot()
    with pytest.raises(FrozenInstanceError):
        snap.symbol = "MSFT"  # type: ignore[misc]


def test_casnapshot_frozen_splits() -> None:
    snap = _make_snapshot()
    with pytest.raises(FrozenInstanceError):
        snap.splits = ()  # type: ignore[misc]


def test_casnapshot_frozen_snapshot_id() -> None:
    snap = _make_snapshot()
    with pytest.raises(FrozenInstanceError):
        snap.snapshot_id = "deadbeef"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CaSnapshot: not hashable
# ---------------------------------------------------------------------------


def test_casnapshot_not_hashable() -> None:
    """hash() must raise TypeError — the dict field blocks hashing.

    This test pins the non-hashable contract.  No caller should attempt to use
    a CaSnapshot as a dict key or set member; if one does, this test failing
    first is a signal that the dataclass design has changed.
    """
    snap = _make_snapshot()
    with pytest.raises(TypeError):
        hash(snap)


# ---------------------------------------------------------------------------
# compute_snapshot_id: ordering invariance
# ---------------------------------------------------------------------------


def test_compute_snapshot_id_ordering_invariant_splits() -> None:
    """Splits in different order produce the same digest."""
    splits_ab = [_SPLIT_A, _SPLIT_B]
    splits_ba = [_SPLIT_B, _SPLIT_A]
    assert compute_snapshot_id(splits_ab, []) == compute_snapshot_id(splits_ba, [])


def test_compute_snapshot_id_ordering_invariant_dividends() -> None:
    """Dividends in different order produce the same digest."""
    divs_ab = [_DIV_A, _DIV_B]
    divs_ba = [_DIV_B, _DIV_A]
    assert compute_snapshot_id([], divs_ab) == compute_snapshot_id([], divs_ba)


def test_compute_snapshot_id_ordering_invariant_mixed() -> None:
    splits_ab = [_SPLIT_A, _SPLIT_B]
    splits_ba = [_SPLIT_B, _SPLIT_A]
    divs_ab = [_DIV_A, _DIV_B]
    divs_ba = [_DIV_B, _DIV_A]
    assert (
        compute_snapshot_id(splits_ab, divs_ab)
        == compute_snapshot_id(splits_ba, divs_ba)
    )


# ---------------------------------------------------------------------------
# compute_snapshot_id: stable across processes
# ---------------------------------------------------------------------------


def test_compute_snapshot_id_stable_across_processes() -> None:
    """Two independent Python processes compute the same digest for the same input."""
    code = (
        "from datetime import date; from decimal import Decimal; "
        "from manta_trading.data.adjustment.k_factor import Split, Dividend, compute_snapshot_id; "
        "splits = [Split('AAPL', date(2020, 8, 31), Decimal('4'), Decimal('1'))]; "
        "divs = [Dividend('AAPL', date(2021, 2, 5), Decimal('0.22'))]; "
        "print(compute_snapshot_id(splits, divs))"
    )
    result1 = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    result2 = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    digest1 = result1.stdout.strip()
    digest2 = result2.stdout.strip()
    assert len(digest1) == 64, f"unexpected digest length: {digest1!r}"
    assert digest1 == digest2, "digests differ across processes"


def test_compute_snapshot_id_matches_in_process_result() -> None:
    """Subprocess result matches the in-process compute."""
    splits = [_SPLIT_A]
    divs = [_DIV_A]
    in_process = compute_snapshot_id(splits, divs)

    code = (
        "from datetime import date; from decimal import Decimal; "
        "from manta_trading.data.adjustment.k_factor import Split, Dividend, compute_snapshot_id; "
        f"splits = [Split('AAPL', date(2020, 8, 31), Decimal('4'), Decimal('1'))]; "
        f"divs = [Dividend('AAPL', date(2021, 2, 5), Decimal('0.22'))]; "
        "print(compute_snapshot_id(splits, divs))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == in_process


# ---------------------------------------------------------------------------
# compute_snapshot_id: fetched_at canary
# ---------------------------------------------------------------------------


def test_compute_snapshot_id_ignores_fetched_at() -> None:
    """Canary: adding a synthetic fetched_at attr to Split does not change the digest.

    Split does not have a fetched_at field by design (slice 143, D4).  If
    it ever gains one in the future, this test ensures compute_snapshot_id
    is not accidentally reading it.  We monkey-patch the attribute onto an
    existing Split instance's underlying dict to simulate the scenario.
    """
    split_normal = Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))
    # Simulate a split with a fetched_at attribute attached externally.
    # object.__setattr__ bypasses frozen=True so we can add a synthetic attr.
    split_with_attr = Split(SYM, date(2020, 8, 31), Decimal("4"), Decimal("1"))
    object.__setattr__(split_with_attr, "fetched_at", "2026-01-01T00:00:00Z")

    digest_normal = compute_snapshot_id([split_normal], [])
    digest_with_attr = compute_snapshot_id([split_with_attr], [])
    assert digest_normal == digest_with_attr, (
        "compute_snapshot_id read fetched_at — it must only use "
        "ex_date, ratio_to, ratio_from for splits"
    )


# ---------------------------------------------------------------------------
# compute_snapshot_id: empty inputs
# ---------------------------------------------------------------------------


def test_compute_snapshot_id_empty() -> None:
    """Empty inputs produce a valid 64-char hex digest (not empty string)."""
    result = compute_snapshot_id([], [])
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_compute_snapshot_id_returns_hex_string() -> None:
    result = compute_snapshot_id([_SPLIT_A], [_DIV_A])
    assert isinstance(result, str)
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)
