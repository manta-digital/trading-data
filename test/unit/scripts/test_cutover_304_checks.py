"""``scripts/cutover_304_checks.py`` (slice 304 D3, D6, D7)."""

from __future__ import annotations

import copy
import importlib
import subprocess
import sys
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
    assert "set-tick-spend-ceilings.sh --per-pass 7 --cap-30d 16" in str(exc.value)


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
        "verdict": "allowed",
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


def test_the_wanted_set_comes_from_the_universe(checks: ModuleType) -> None:
    assert checks.wanted_keys() == {
        "ES/definition",
        "ES/statistics",
        "ES/ohlcv-1m",
        "GC/definition",
        "GC/statistics",
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
        {"schema": "ohlcv-1m", "provider_job_id": "GLBX-1"},
        {"schema": "ohlcv-1m", "provider_job_id": None},
        {"schema": "statistics", "provider_job_id": "GLBX-2"},
    ]
    assert checks.bar_job_dirs(rows) == [checks.ARCHIVE_ROOT / "GLBX-1"]


@pytest.mark.parametrize(("code", "outcome"), [(0, "pass"), (1, "finding")])
def test_session_check_exits_zero_and_one_are_pass_and_finding(
    checks: ModuleType, monkeypatch: pytest.MonkeyPatch, code: int, outcome: str
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, code)
    )
    assert checks.run_session_check([Path("/x")]) == outcome


def test_session_check_exit_two_stops(
    checks: ModuleType, cutover_error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 2)
    )
    with pytest.raises(cutover_error, match="exit 2"):
        checks.run_session_check([Path("/x")])
