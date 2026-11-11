"""Slice 307: the accounting side of the cutover's evidence (D6 items 2, 4, 6)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module(name)


@pytest.fixture(scope="module")
def pa() -> ModuleType:
    return _load("purchase_accounting")


@pytest.fixture(scope="module")
def cutover_error() -> type[Exception]:
    error: type[Exception] = _load("cutover_common").CutoverError
    return error


def _line(
    family: str, schema: str | None, *, complete: int, over: int | None, records: int
) -> Any:
    ta = importlib.import_module("manta_trading.data.tick.tick_accounting")
    ct = importlib.import_module("manta_trading.data.tick.cross_tier_result")
    sf = importlib.import_module("manta_trading.data.tick.schema_families")
    results = (
        ()
        if over is None
        else (
            ct.CrossTierResult(
                "traded_vs_cleared", sf.SchemaFamily.BAR_1M, 90, 5, over, 0
            ),
        )
    )
    return ta.TickAccountingLine(
        "ES", family, schema, 10, complete, {"complete": complete},
        records, complete == 10, None, False, results,
    )  # fmt: skip


def _document(over: int | None = 0, records: int = 100) -> dict[str, Any]:
    """The real ``--json`` shape: ``accounting_futures._product_payload``."""
    af = importlib.import_module("manta_trading.cli.commands.accounting_futures")
    lines = [
        _line("tick_tier", "tbbo", complete=10, over=None, records=5),
        _line("bar_1m", "ohlcv-1m", complete=8, over=over, records=records),
    ]
    return {"rows": [], "tick": {"products": [af._product_payload("ES", lines)]}}


def test_one_row_per_product_and_family_with_the_three_values(pa: ModuleType) -> None:
    rows = pa.accounting_rows(_document())
    assert [r.label for r in rows] == ["tbbo", "ohlcv-1m"]
    lines = pa.narration(rows)
    assert "sessions 8/10" in lines[1] and "caught up False" in lines[1]
    assert "source edge None" in lines[1]
    assert "traded_vs_cleared: agree 90 under 5 over 0 no_trades 0" in lines[1]


def test_zero_over_passes_and_a_count_fails_naming_the_family(pa: ModuleType) -> None:
    assert pa.over_failures(pa.accounting_rows(_document(over=0)), {}) == []
    (failure,) = pa.over_failures(
        pa.accounting_rows(_document(over=2)), {"ES": ["2026-11-03 ESZ6 (traded 9)"]}
    )
    assert "ES ohlcv-1m: 2 sessions over" in failure
    assert "2026-11-03 ESZ6" in failure


def test_over_sessions_are_named_from_a_statistics_coverage_document(
    pa: ModuleType,
) -> None:
    doc = {
        "volume_checks": [
            {"session_date": "2026-11-03", "raw_symbol": "ESZ6", "traded": 9,
             "cleared": 5, "verdict": "over"},
            {"session_date": "2026-11-04", "raw_symbol": "ESZ6", "traded": 4,
             "cleared": 5, "verdict": "under"},
        ]
    }  # fmt: skip
    assert pa.over_sessions(doc) == ["2026-11-03 ESZ6 (traded 9, cleared 5)"]


def test_an_over_session_without_a_raw_symbol_names_its_instrument(
    pa: ModuleType,
) -> None:
    doc = {
        "volume_checks": [
            {"session_date": "2026-11-03", "raw_symbol": None, "instrument_id": 42,
             "traded": 9, "cleared": 5, "verdict": "over"},
        ]
    }  # fmt: skip
    assert pa.over_sessions(doc) == ["2026-11-03 42 (traded 9, cleared 5)"]


def test_a_missing_key_is_a_stop_not_a_default(
    pa: ModuleType, cutover_error: type[Exception]
) -> None:
    doc = _document()
    del doc["tick"]["products"][0]["families"][1]["caught_up"]
    with pytest.raises(cutover_error, match="caught_up"):
        pa.accounting_rows(doc)


def test_an_unavailable_tick_section_is_a_stop(
    pa: ModuleType, cutover_error: type[Exception]
) -> None:
    doc = {"tick": {"state": "no_database", "detail": "", "products": []}}
    with pytest.raises(cutover_error, match="unavailable: no_database"):
        pa.accounting_rows(doc)


def test_a_family_that_is_not_there_is_a_stop(
    pa: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="GC statistics"):
        pa.row_for(pa.accounting_rows(_document()), "GC", "statistics")


def test_before_and_after_totals_give_the_delta_per_family(pa: ModuleType) -> None:
    before = pa.record_totals(pa.accounting_rows(_document(records=100)))
    after = pa.record_totals(pa.accounting_rows(_document(records=350)))
    assert pa.totals_delta(before, after)["ES/bar_1m"] == (100, 350, 250)


def test_a_family_with_no_baseline_total_is_a_stop(
    pa: ModuleType, cutover_error: type[Exception]
) -> None:
    after = pa.record_totals(pa.accounting_rows(_document()))
    with pytest.raises(cutover_error, match="ES/bar_1m"):
        pa.totals_delta({"ES/tick_tier": 5}, after)
