"""Tests for ``scripts/measure_tick_bars.py``'s own logic (slice 308, task 7.2).

``pick_range`` and ``check_totals`` over synthetic ledger rows, and ``judge``'s
bounds. The production read and request run only in Phase 7 (#75).
"""

from __future__ import annotations

from datetime import date
from types import ModuleType
from typing import Any

import pytest
from tick_support.measure_script import measure_script


@pytest.fixture(scope="module")
def measure() -> ModuleType:
    return measure_script()


def _rows(measure: ModuleType, counts: list[int]) -> list[Any]:
    return [
        measure.LedgerRow(date(2024, 12, 2 + i), 5002, n, n * 2)
        for i, n in enumerate(counts)
    ]


def _bars(rows: list[Any], n: int) -> list[dict[str, Any]]:
    """Bars of ``n`` trades per session, the last one partial."""
    bars = []
    for row in rows:
        left = row.records
        while left > 0:
            size = min(n, left)
            bars.append(
                {
                    "session_date": row.session_date.isoformat(),
                    "instrument_id": row.instrument_id,
                    "trade_count": size,
                    "volume": size * 2,
                }
            )
            left -= size
    return bars


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ([400, 300, 260, 500], [400, 300, 260]),  # 960 of 1000
        ([700, 400, 380, 590], [380, 590]),  # skips runs over the limit
        ([1000], [1000]),  # exactly the limit
        ([10, 950, 990], [10, 950]),  # the first run in the band wins
    ],
)
def test_pick_range_returns_the_first_run_in_the_band(
    measure: ModuleType, counts: list[int], expected: list[int]
) -> None:
    picked = measure.pick_range(_rows(measure, counts), 1000)
    assert [row.records for row in picked] == expected
    assert 950 <= sum(expected) <= 1000


@pytest.mark.parametrize("counts", [[], [100, 200, 300], [1001], [500, 600, 700]])
def test_pick_range_raises_when_no_run_is_in_the_band(
    measure: ModuleType, counts: list[int]
) -> None:
    with pytest.raises(ValueError, match="no run of consecutive sessions"):
        measure.pick_range(_rows(measure, counts), 1000)


def test_check_totals_accepts_matching_totals(measure: ModuleType) -> None:
    rows = _rows(measure, [2500, 1000, 1])
    assert measure.check_totals(_bars(rows, 1000), rows, 1000) == []


def test_check_totals_names_the_session_on_a_trade_mismatch(
    measure: ModuleType,
) -> None:
    rows = _rows(measure, [2500, 1000])
    bars = _bars(rows, 1000)
    bars[-1]["trade_count"] -= 1
    failures = measure.check_totals(bars, rows, 1000)
    assert failures == ["2024-12-03 5002: Σ trade_count 999 != ledger records 1,000"]


def test_check_totals_names_the_session_on_a_volume_mismatch(
    measure: ModuleType,
) -> None:
    rows = _rows(measure, [2500])
    bars = _bars(rows, 1000)
    bars[0]["volume"] += 7
    failures = measure.check_totals(bars, rows, 1000)
    assert failures == ["2024-12-02 5002: Σ volume 5,007 != ledger volume 5,000"]


def test_check_totals_flags_a_wrong_bar_count(measure: ModuleType) -> None:
    rows = _rows(measure, [2500])
    bars = _bars(rows, 500)  # right totals, twice the bars
    assert measure.check_totals(bars, rows, 1000) == ["bar count 5 != Σ ceil(nᵢ / N) 3"]


def test_check_totals_flags_bars_outside_the_range(measure: ModuleType) -> None:
    rows = _rows(measure, [1000])
    stray = measure.LedgerRow(date(2024, 12, 9), 5002, 0, 0)
    bars = _bars(rows, 1000) + [
        {
            "session_date": "2024-12-09",
            "instrument_id": 5002,
            "trade_count": 0,
            "volume": 0,
        }
    ]
    failures = measure.check_totals(bars, rows, 1000)
    assert f"{stray.session_date} 5002: bars outside the picked range" in failures


def test_judge_applies_both_bounds(measure: ModuleType) -> None:
    fast = measure.Timing(2.0, [0.06] * 7, [0.15] * 7)
    assert measure.judge(fast, []) == []
    slow = measure.Timing(measure.BOUND_S, [0.06] * 7, [0.40] * 7)
    failures = measure.judge(slow, ["totals"])
    assert failures[0] == "totals"
    assert any("bound" in f for f in failures)
    assert any("catalog probe" in f for f in failures)
    unprobed = measure.Timing(1.0, [0.06] * 7, [0.15, 0.15])
    assert measure.judge(unprobed, []) == [
        "only 2 probe(s) ran beside the request, fewer than 3"
    ]


@pytest.mark.parametrize("value", ["0", "-5", "ten"])
@pytest.mark.parametrize("flag", ["--ticks-per-bar", "--limit"])
def test_bad_numeric_arguments_are_usage_errors(
    measure: ModuleType, flag: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--base-url", "http://x", "--product", "ES", "--rule", "v.0",
            "--start", "2024-09-03", "--end", "2025-01-01",
            "--ticks-per-bar", "1000", "--limit", "2000000"]  # fmt: skip
    argv[argv.index(flag) + 1] = value
    with pytest.raises(SystemExit) as exited:
        measure.main(argv)
    assert exited.value.code == 2
    assert flag in capsys.readouterr().err
