"""Slice 307: the issue gate and the dataset-edge check of the purchase cutover."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module(name)


@pytest.fixture(scope="module")
def pv() -> ModuleType:
    return _load("purchase_verdicts")


@pytest.fixture(scope="module")
def cutover_error() -> type[Exception]:
    error: type[Exception] = _load("cutover_common").CutoverError
    return error


# -- the issue gate ---------------------------------------------------------------


def test_a_closed_issue_passes(pv: ModuleType) -> None:
    pv.check_gate_issue(69, lambda n: json.dumps({"state": "CLOSED"}))


def test_an_open_issue_stops_naming_it(
    pv: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match=r"#69 is OPEN.*invoice comparison"):
        pv.check_gate_issue(69, lambda n: json.dumps({"state": "OPEN"}))


def test_a_gh_failure_stops_and_is_never_read_as_closed(
    pv: ModuleType, cutover_error: type[Exception]
) -> None:
    def failing(number: int) -> str:
        raise subprocess.CalledProcessError(1, ["gh"])

    with pytest.raises(cutover_error, match="gh failed"):
        pv.check_gate_issue(69, failing)


@pytest.mark.parametrize(
    ("text", "message"),
    [("not json", "no JSON"), ("{}", "no state"), ("[]", "no state")],
)
def test_unreadable_gh_output_stops(
    pv: ModuleType, cutover_error: type[Exception], text: str, message: str
) -> None:
    with pytest.raises(cutover_error, match=message):
        pv.check_gate_issue(69, lambda n: text)


# -- the dataset edge -------------------------------------------------------------

MINIMUM = datetime(2026, 12, 1, tzinfo=UTC)


def _status(edge: object, code: int = 0, stderr: str = "") -> object:
    products = [{"product": "ES", "edge_available_end": edge}]
    body = json.dumps({"products": products})
    return subprocess.CompletedProcess(["status"], code, body, stderr)


def test_an_edge_equal_to_the_minimum_passes_and_a_second_earlier_stops(
    pv: ModuleType, cutover_error: type[Exception]
) -> None:
    pv.check_dataset_edge(_status(MINIMUM.isoformat()), MINIMUM)
    early = (MINIMUM - timedelta(seconds=1)).isoformat()
    with pytest.raises(cutover_error, match="before the latest want end"):
        pv.check_dataset_edge(_status(early), MINIMUM)


def test_every_product_must_reach_the_minimum(
    pv: ModuleType, cutover_error: type[Exception]
) -> None:
    products = [
        {"product": "ES", "edge_available_end": MINIMUM.isoformat()},
        {"product": "GC", "edge_available_end": "2026-11-30T00:00:00+00:00"},
    ]
    result = subprocess.CompletedProcess(["s"], 0, json.dumps({"products": products}))
    with pytest.raises(cutover_error, match="before the latest want end"):
        pv.check_dataset_edge(result, MINIMUM)


def test_a_status_verdict_exit_is_not_a_failure(pv: ModuleType) -> None:
    pv.check_dataset_edge(_status(MINIMUM.isoformat(), code=3), MINIMUM)


def test_a_failing_status_stops_naming_the_code(
    pv: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="status exited 4: storage down"):
        pv.check_dataset_edge(_status(None, 4, "storage down"), MINIMUM)


@pytest.mark.parametrize(
    ("edge", "message"),
    [(None, "no available end"), ("yesterday", "'yesterday' is not an instant")],
)
def test_a_null_or_unparseable_edge_stops_with_its_own_message(
    pv: ModuleType, cutover_error: type[Exception], edge: object, message: str
) -> None:
    with pytest.raises(cutover_error, match=message):
        pv.check_dataset_edge(_status(edge), MINIMUM)


def test_an_absent_edge_key_stops(
    pv: ModuleType, cutover_error: type[Exception]
) -> None:
    body = json.dumps({"products": [{"product": "ES"}]})
    with pytest.raises(cutover_error, match="no available end"):
        pv.check_dataset_edge(subprocess.CompletedProcess(["s"], 0, body), MINIMUM)


def test_a_naive_instant_is_not_utc(
    pv: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="not a UTC instant"):
        pv.check_dataset_edge(_status("2026-12-01T00:00:00"), MINIMUM)


@pytest.mark.parametrize("stdout", ["nope", "{}", '{"products": []}'])
def test_unreadable_status_output_stops(
    pv: ModuleType, cutover_error: type[Exception], stdout: str
) -> None:
    result = subprocess.CompletedProcess(["s"], 0, stdout)
    with pytest.raises(cutover_error, match="dataset edge"):
        pv.check_dataset_edge(result, MINIMUM)


# -- the minimum edge comes from the universe ---------------------------------------


def test_latest_want_end_follows_the_universe(
    pv: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = pv.TICK_UNIVERSE
    base = universe[0]
    want = base.history[0]
    changed = base.__class__(
        "ES",
        base.symbols,
        base.stype_in,
        tier=base.tier,
        start=base.start,
        end=base.end,
        history=(want.__class__(want.schema, want.start, date(2027, 3, 1)),),
    )
    monkeypatch.setattr(pv, "TICK_UNIVERSE", (changed,))
    assert pv.latest_want_end() == datetime(2027, 3, 1, tzinfo=UTC)


def test_latest_want_end_matches_the_real_universe(pv: ModuleType) -> None:
    ends = [w.end for e in pv.TICK_UNIVERSE for w in e.history]
    assert pv.latest_want_end().date() == max(ends)
    assert pv.latest_want_end().tzinfo is UTC
