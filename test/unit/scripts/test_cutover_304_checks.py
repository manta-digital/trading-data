"""``scripts/cutover_304_checks.py`` (slice 304 D3, D6, D7)."""

from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


@pytest.fixture(scope="module")
def checks() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("cutover_304_checks")


@pytest.fixture
def cutover_error() -> type[Exception]:
    return importlib.import_module("cutover_common").CutoverError


ENV = "MT_TICK_SPEND_CEILING_USD=7.00\nMT_TICK_SPEND_30D_CEILING_USD=16.00\nOTHER=x\n"


def test_preflight_returns_the_per_pass_ceiling(checks: ModuleType) -> None:
    assert checks.check_env_text(ENV) == Decimal("7.00")


def test_preflight_refuses_a_set_plan(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="MT_TICK_DATABENTO_PLAN"):
        checks.check_env_text(ENV + "MT_TICK_DATABENTO_PLAN=standard\n")


@pytest.mark.parametrize(
    "drop", ["MT_TICK_SPEND_CEILING_USD", "MT_TICK_SPEND_30D_CEILING_USD"]
)
def test_preflight_names_the_ceiling_command_when_a_ceiling_is_missing(
    checks: ModuleType, cutover_error: type[Exception], drop: str
) -> None:
    text = "\n".join(ln for ln in ENV.splitlines() if not ln.startswith(drop + "="))
    with pytest.raises(cutover_error) as exc:
        checks.check_env_text(text)
    assert drop in str(exc.value)
    assert "--estimate run prints" in str(exc.value)


def _summary() -> dict[str, Any]:
    """The key shape of ``plan_summary`` (purchase_plan.py), one clean request."""
    return {
        "wanted_days": {"ES/statistics": 10, "GC/definition": 10},
        "pending_days": {},
        "missing_days": {},
        "requests": [
            {
                "schema": "statistics",
                "start": "2024-01-01",
                "end": "2024-02-01",
                "days": 10,
                "cost_usd": "1.5",
                "resubmit": False,
            }
        ],
        "planned_usd": "6.3",
        "trailing_30d_usd": "8.5",
        "unheld_jobs": [],
        "per_pass_ceiling_usd": "7.00",
        "cap_30d_usd": "16.00",
        "verdict": "estimate_only",
        "reasons": [],
    }


def test_a_clean_estimate_may_be_bought(checks: ModuleType) -> None:
    assert checks.judge_estimate(_summary()) is checks.Estimate.BUY


def test_nothing_to_buy_is_its_own_outcome(checks: ModuleType) -> None:
    summary = _summary() | {
        "requests": [],
        "planned_usd": "0",
        "verdict": "nothing_to_buy",
    }
    assert checks.judge_estimate(summary) is checks.Estimate.NOTHING_TO_BUY


def _mutated(change: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(_summary()) | change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"planned_usd": "7.01"}, "per-pass"),
        ({"planned_usd": "7.0", "trailing_30d_usd": "9.5"}, "30-day"),
        ({"verdict": "refused", "reasons": ["x"]}, "verdict refused"),
        ({"wanted_days": {"ES/tbbo": 3}}, "ES/tbbo"),
    ],
)
def test_an_unbuyable_estimate_refuses_and_names_the_cause(
    checks: ModuleType,
    cutover_error: type[Exception],
    change: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(cutover_error, match=message):
        checks.judge_estimate(_mutated(change))


def test_a_tbbo_request_and_a_resubmit_refuse(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    request = _summary()["requests"][0]
    with pytest.raises(cutover_error, match="tbbo"):
        checks.judge_estimate(_mutated({"requests": [request | {"schema": "tbbo"}]}))
    with pytest.raises(cutover_error, match="resubmit"):
        checks.judge_estimate(_mutated({"requests": [request | {"resubmit": True}]}))


@pytest.mark.parametrize(
    ("planned", "trailing", "per_pass", "cap"),
    [("89", "0", "90", "90"), ("90", "0", "90", "90"), ("6.3", "4", "10", "15")],
)
def test_ceiling_command_rounds_the_estimate_up_to_the_next_five(
    checks: ModuleType, planned: str, trailing: str, per_pass: str, cap: str
) -> None:
    command = checks.ceiling_command(Decimal(planned), Decimal(trailing))
    assert command.endswith(f"--per-pass {per_pass} --cap-30d {cap}")
    assert str(checks.CEILING_SCRIPT) in command


def test_an_over_ceiling_estimate_carries_the_command_for_its_figures(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error) as exc:
        checks.judge_estimate(_mutated({"planned_usd": "7.01"}))
    assert "--per-pass 10 --cap-30d 20" in str(exc.value)  # 7.01 + 8.5 = 15.51


SINCE = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
RESUBMIT = _summary()["requests"][0] | {"resubmit": True}
RESUBMIT_KEY = ("statistics", "2024-01-01", "2024-02-01")


def _resubmit_summary() -> dict[str, Any]:
    return _mutated({"requests": [RESUBMIT]})


def test_a_resubmit_of_a_request_made_after_since_is_accepted(
    checks: ModuleType,
) -> None:
    origins = {RESUBMIT_KEY: datetime(2026, 11, 9, 12, 30, tzinfo=UTC)}
    result = checks.judge_estimate(_resubmit_summary(), SINCE, origins)
    assert result is checks.Estimate.BUY


def test_a_resubmit_of_a_request_made_before_since_refuses(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    origins = {RESUBMIT_KEY: datetime(2026, 11, 9, 11, 59, tzinfo=UTC)}
    with pytest.raises(cutover_error, match="before this cutover started"):
        checks.judge_estimate(_resubmit_summary(), SINCE, origins)


def test_a_resubmit_with_no_recorded_original_refuses(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="no recorded original"):
        checks.judge_estimate(_resubmit_summary(), SINCE, {})


def test_with_no_start_every_resubmit_refuses_even_one_made_just_now(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    origins = {RESUBMIT_KEY: datetime.now(UTC)}
    with pytest.raises(cutover_error, match="is a resubmit"):
        checks.judge_estimate(_resubmit_summary(), None, origins)


def _request_row(
    job: str | None, made: str, start: str = "2024-01-01"
) -> dict[str, Any]:
    return {
        "request_id": 1,
        "schema": "statistics",
        "range_start": start,
        "range_end": "2024-02-01",
        "provider_job_id": job,
        "requested_at": made,
    }


def test_resubmit_origins_use_jobless_rows_and_the_earliest_on_a_shared_range(
    checks: ModuleType,
) -> None:
    rows = [
        _request_row(None, "2026-11-09T13:00:00+00:00"),
        _request_row(None, "2026-11-09T12:10:00+00:00"),
        _request_row("GLBX-1", "2020-01-01T00:00:00+00:00"),
    ]
    assert checks.resubmit_origins(rows) == {
        RESUBMIT_KEY: datetime(2026, 11, 9, 12, 10, tzinfo=UTC)
    }


def test_an_unparseable_request_time_is_a_stop(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="not a timestamp"):
        checks.resubmit_origins([_request_row(None, "yesterday")])


def test_the_wanted_set_comes_from_the_universe(checks: ModuleType) -> None:
    assert checks.wanted_keys() == {
        "ES/definition",
        "ES/statistics",
        "ES/ohlcv-1m",
        "GC/definition",
        "GC/statistics",
        "GC/ohlcv-1m",
    }


@pytest.mark.parametrize(
    ("state", "reason", "expected"),
    [
        ("verified", None, "awaiting definitions"),
        (
            "verified",
            "stat_reference: instrument_id 1 stat_type 2: not a session",
            "stat_reference",
        ),
        ("downloaded", "session_boundary: 3 records in no session", "session_boundary"),
        (
            "downloaded",
            "ESZ9 has no validity window (activation or expiration undefined)",
            "undefined definition window",
        ),
        ("submitted", None, "unclassified"),
        ("verified", "decode: boom", "unclassified"),
    ],
)
def test_classifier_covers_each_d6_class_and_shows_the_rest(
    checks: ModuleType, state: str, reason: str | None, expected: str
) -> None:
    row = {"state": state, "failure_reason": reason}
    assert checks.classify_unit(row) == expected


def test_only_unfinished_units_are_counted(checks: ModuleType) -> None:
    rows = [
        {"state": "ingested", "failure_reason": None},
        {"state": "verified", "failure_reason": None},
    ]
    counts = checks.classify_unfinished(rows)
    assert dict(counts) == {"awaiting definitions": 1}


def test_bar_job_dirs_come_from_the_ohlcv_requests_only(checks: ModuleType) -> None:
    rows = [
        {"schema": "ohlcv-1m", "provider_job_id": "GLBX-1", "symbols": "ES.FUT"},
        {"schema": "ohlcv-1m", "provider_job_id": None, "symbols": "ES.FUT"},
        {"schema": "statistics", "provider_job_id": "GLBX-2", "symbols": "ES.FUT"},
    ]
    assert checks.bar_job_dirs(rows) == {"ES": [checks.ARCHIVE_ROOT / "GLBX-1"]}


def test_bar_job_dirs_group_by_product(checks: ModuleType) -> None:
    rows = [
        {"schema": "ohlcv-1m", "provider_job_id": "GLBX-2", "symbols": "GC.FUT"},
        {"schema": "ohlcv-1m", "provider_job_id": "GLBX-1", "symbols": "ES.FUT"},
        {"schema": "ohlcv-1m", "provider_job_id": "GLBX-3", "symbols": "GC.FUT"},
    ]
    root = checks.ARCHIVE_ROOT
    assert checks.bar_job_dirs(rows) == {
        "ES": [root / "GLBX-1"],
        "GC": [root / "GLBX-2", root / "GLBX-3"],
    }


def test_bar_job_dirs_refuse_symbols_outside_the_universe(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    rows = [{"schema": "ohlcv-1m", "provider_job_id": "J", "symbols": "ZZ.FUT"}]
    with pytest.raises(cutover_error, match="ZZ.FUT"):
        checks.bar_job_dirs(rows)


def test_bar_job_dirs_read_the_rows_the_real_cli_renders(
    checks: ModuleType,
) -> None:
    """``debug requests --json`` renders ``symbols`` through ``_cell``."""
    cell = importlib.import_module("manta_trading.cli.commands.tick_debug_cmds")._cell
    row = {"schema": "ohlcv-1m", "provider_job_id": "J", "symbols": ["ES.FUT"]}
    rendered = {k: cell(k, v) for k, v in row.items()}
    assert checks.bar_job_dirs([rendered]) == {"ES": [checks.ARCHIVE_ROOT / "J"]}


def test_each_product_has_its_own_calendar(checks: ModuleType) -> None:
    constants = importlib.import_module("manta_trading.data.tick.constants")
    assert checks.calendar_for("ES") == constants.CME_EQUITY_CALENDAR_ID
    assert checks.calendar_for("GC") == constants.CME_METALS_CALENDAR_ID
    assert checks.calendar_for("ES") != checks.calendar_for("GC")


def test_an_unmapped_product_has_no_default_calendar(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="ZZ"):
        checks.calendar_for("ZZ")


@pytest.mark.parametrize(("code", "outcome"), [(0, "pass"), (1, "finding")])
def test_session_check_exits_zero_and_one_are_pass_and_finding(
    checks: ModuleType, monkeypatch: pytest.MonkeyPatch, code: int, outcome: str
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, code)
    )
    assert checks.run_session_check([Path("/x")], "CAL") == outcome


def test_session_check_passes_the_calendar_it_is_given(
    checks: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake)
    checks.run_session_check([Path("/x")], "CME_METALS")
    assert seen[0][seen[0].index("--calendar") + 1] == "CME_METALS"


def test_session_check_exit_two_stops(
    checks: ModuleType, cutover_error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 2)
    )
    with pytest.raises(cutover_error, match="exit 2"):
        checks.run_session_check([Path("/x")], "CAL")


def test_checkout_database_must_be_productions(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    prod = "MT_TICK_DB_URL=postgresql://u:p@db.example:5432/tick\n"
    same = "MT_TICK_DB_URL=postgresql://x:y@db.example:5432/tick\n"
    other = "MT_TICK_DB_URL=postgresql://u:p@localhost:5432/tick_dev\n"
    assert checks.check_checkout_database(same, prod) == "tick"
    with pytest.raises(cutover_error, match="tick_dev"):
        checks.check_checkout_database(other, prod)


# -- the real ``pass --estimate-only --json`` payload -------------------------------

REAL_PAYLOAD = Path(__file__).parent / "fixtures" / "pass_estimate_only.json"


def test_purchase_summary_reads_the_real_pass_payload(checks: ModuleType) -> None:
    """A captured ``mt-run data tick pass --estimate-only --json`` stdout."""
    summary = checks.purchase_summary(json.loads(REAL_PAYLOAD.read_text()))
    assert summary["verdict"] == "nothing_to_buy"
    assert checks.judge_estimate(summary) is checks.Estimate.NOTHING_TO_BUY


def test_a_planned_estimate_reports_estimate_only_and_is_buyable(
    checks: ModuleType,
) -> None:
    payload = json.loads(REAL_PAYLOAD.read_text())
    purchase = next(p for p in payload["phases"] if p["name"] == "purchase")
    purchase["summary"] = _summary()
    assert checks.judge_estimate(checks.purchase_summary(payload)) is (
        checks.Estimate.BUY
    )


@pytest.mark.parametrize("outcome", ["failed", "skipped"])
def test_a_purchase_phase_that_did_not_succeed_is_a_stop(
    checks: ModuleType, cutover_error: type[Exception], outcome: str
) -> None:
    payload = json.loads(REAL_PAYLOAD.read_text())
    next(p for p in payload["phases"] if p["name"] == "purchase")["outcome"] = outcome
    with pytest.raises(cutover_error, match="purchase phase"):
        checks.purchase_summary(payload)


def test_a_payload_without_a_purchase_phase_is_a_stop(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    with pytest.raises(cutover_error, match="0 purchase phases"):
        checks.purchase_summary({"phases": []})


def test_a_naive_requested_at_is_a_stop_not_a_type_error(
    checks: ModuleType, cutover_error: type[Exception]
) -> None:
    row = {
        "request_id": 1,
        "schema": "ohlcv-1m",
        "provider_job_id": None,
        "range_start": "2026-11-01",
        "range_end": "2026-12-01",
        "requested_at": "2026-12-02T10:00:00",
    }
    with pytest.raises(cutover_error, match="not a UTC instant"):
        checks.resubmit_origins([row])
