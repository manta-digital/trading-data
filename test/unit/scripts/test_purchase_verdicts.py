"""Slice 307: the issue gate and the dataset-edge check of the purchase cutover."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
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


def test_an_unknown_session_bucket_is_a_stop(
    pv: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="not one the exit rule knows"):
        pv._unaccounted("brand-new-status", None, [])


# -- D5: failed requests (3.1) and the not-caught-up list (4.1) ----------------------


def _request(request_id: int, symbols: str, start: str, end: str) -> dict:
    """A ``debug requests`` row, keyed as ``debug_reads._REQUESTS`` selects it."""
    return {
        "request_id": request_id,
        "schema": "ohlcv-1m",
        "symbols": symbols,
        "range_start": start,
        "range_end": end,
        "provider_job_id": None,
        "requested_at": "2026-12-02T10:00:00+00:00",
    }


def _unit(
    unit_id: int,
    request_id: int,
    *,
    fetch_status: str = "UNKNOWN",
    state: str = "verified",
    reason: str | None = None,
) -> dict:
    """A ``debug units`` row (``debug_reads._UNITS``), enums as their values."""
    return {
        "unit_id": unit_id,
        "request_id": request_id,
        "unit_date": "2026-11-02",
        "schema": "ohlcv-1m",
        "state": state,
        "fetch_status": fetch_status,
        "failure_reason": reason,
    }


def test_a_failed_retryable_request_fails_naming_it_and_exhausted_ones_do_not(
    pv: ModuleType,
) -> None:
    units = [
        _unit(1, 7, fetch_status="FAILED_RETRYABLE", state="requested"),
        _unit(2, 7, fetch_status="FAILED_RETRYABLE", state="requested"),
        _unit(3, 8, fetch_status="RETRY_EXHAUSTED", state="requested", reason="422"),
    ]
    failures = pv.retryable_failures(units)
    assert len(failures) == 1
    assert "request 7" in failures[0]


def test_a_retry_exhausted_request_is_listed_with_its_error_and_label(
    pv: ModuleType,
) -> None:
    requests = [_request(8, "GC.FUT", "2026-11-01", "2026-12-01")]
    units = [
        _unit(3, 8, fetch_status="RETRY_EXHAUSTED", state="requested", reason="422 x"),
        _unit(4, 8, fetch_status="RETRY_EXHAUSTED", state="requested", reason="422 x"),
        _unit(5, 9, fetch_status="UNKNOWN"),
    ]
    (found,) = pv.retry_exhausted_requests(requests, units)
    assert (found.request_id, found.product, found.error) == (8, "GC", "422 x")
    assert "acquisition failed" in found.text()
    assert "422 x" in found.text()
    assert all(pv.unit_is_retry_exhausted(u) for u in units[:2])
    assert not pv.unit_is_retry_exhausted(units[2])


def _coverage(schema: str, *sessions: tuple[str, str, tuple[int, ...]]) -> dict:
    """The real ``coverage --json`` document (``coverage_to_dict``)."""
    cov = importlib.import_module("manta_trading.data.tick.coverage_rows")
    tick_coverage = importlib.import_module("manta_trading.data.tick.tick_coverage")
    rows = tuple(
        cov.CoverageSession(date.fromisoformat(day), "available", status, ids, 0, 0, ())
        for day, status, ids in sessions
    )
    render = importlib.import_module("manta_trading.cli.commands.tick_status_render")
    result = tick_coverage.TickCoverage(
        "ES", "CME_EQUITY", date(2026, 11, 2), date(2026, 12, 1), rows, schema=schema
    )
    return render.coverage_to_dict([result], 0)


STATS = "statistics"


def _report(
    pv: ModuleType,
    *sessions: tuple[str, str, tuple[int, ...]],
    schema: str = STATS,
    caught_up: bool | None = False,
    units: list[dict] | None = None,
    exhausted: list | None = None,
) -> Any:
    coverage = _coverage(schema, *sessions)
    return pv.not_caught_up_report(
        ("ES", schema, caught_up), coverage, units or [], exhausted or []
    )


def test_complete_sessions_and_provider_holes_are_not_listed(pv: ModuleType) -> None:
    report = _report(
        pv,
        ("2026-11-02", "complete", (1,)),
        ("2026-11-03", "provider_hole", ()),
    )
    assert report.lines == [] and report.failures == []


@pytest.mark.parametrize("caught_up", [True, None])
def test_a_family_that_is_caught_up_lists_nothing(
    pv: ModuleType, caught_up: bool | None
) -> None:
    report = _report(pv, ("2026-11-02", "missing", ()), caught_up=caught_up)
    assert report.lines == [] and report.failures == []


def test_a_failed_unit_line_carries_its_reason_verbatim(pv: ModuleType) -> None:
    reason = "stat_reference: instrument_id 1 stat_type 2: not a session"
    report = _report(
        pv,
        ("2026-11-02", "failed", (5,)),
        ("2026-11-03", "complete", (6,)),
        units=[_unit(5, 1, reason=reason)],
    )
    assert reason in report.lines[0]
    assert "[stat_reference]" in report.lines[0]
    assert report.failures == []  # a failure with a reason is accounted for


def test_the_last_statistics_session_is_publication_after_want_end(
    pv: ModuleType,
) -> None:
    report = _report(
        pv, ("2026-11-27", "complete", (1,)), ("2026-11-30", "missing", ())
    )
    assert "publication after want end" in report.lines[0]
    assert report.failures == []


def test_the_same_bucket_one_session_earlier_fails(pv: ModuleType) -> None:
    report = _report(
        pv, ("2026-11-27", "missing", ()), ("2026-11-30", "complete", (1,))
    )
    assert report.failures == ["ES statistics 2026-11-27 missing is not accounted for"]


def test_only_statistics_carry_the_publication_label(pv: ModuleType) -> None:
    report = _report(pv, ("2026-11-30", "missing", ()), schema="ohlcv-1m")
    assert report.failures


@pytest.mark.parametrize(
    "bucket", ["failed", "retry_exhausted", "in_flight", "awaiting_ingest", "pending"]
)
def test_each_unaccounted_for_case_fails_on_its_own(
    pv: ModuleType, bucket: str
) -> None:
    report = _report(pv, ("2026-11-02", bucket, ()), ("2026-11-03", "complete", (1,)))
    assert len(report.failures) == 1
    assert bucket in report.failures[0]


def test_edge_unknown_not_at_the_end_fails(pv: ModuleType) -> None:
    report = _report(
        pv, ("2026-11-02", "edge_unknown", ()), ("2026-11-03", "complete", (1,))
    )
    assert len(report.failures) == 1


def test_a_session_inside_a_retry_exhausted_request_is_acquisition_failed(
    pv: ModuleType,
) -> None:
    requests = [_request(8, "ES.FUT", "2026-11-01", "2026-12-01") | {"schema": STATS}]
    units = [_unit(3, 8, fetch_status="RETRY_EXHAUSTED", state="requested", reason="x")]
    exhausted = pv.retry_exhausted_requests(requests, units)
    report = _report(
        pv,
        ("2026-11-02", "retry_exhausted", ()),
        ("2026-11-03", "complete", (1,)),
        exhausted=exhausted,
    )
    assert "acquisition failed" in report.lines[0]
    assert report.failures == []
