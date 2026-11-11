"""``scripts/cutover_304_first_purchase.py`` and ``cutover_304_evidence.py``.

Host calls (``mt-run``, the installer, the timers) are replaced on the loaded
modules; nothing here touches production.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
D = Decimal


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module(name)


@pytest.fixture(scope="module")
def fp() -> ModuleType:
    return _load("cutover_304_first_purchase")


@pytest.fixture(scope="module")
def ev() -> ModuleType:
    return _load("cutover_304_evidence")


@pytest.fixture(scope="module")
def cutover_error() -> type[Exception]:
    error: type[Exception] = _load("cutover_common").CutoverError
    return error


ENV = "MT_TICK_SPEND_CEILING_USD=7\nMT_TICK_SPEND_30D_CEILING_USD=16\n"
NOTHING = {
    "wanted_days": {},
    "requests": [],
    "planned_usd": "0",
    "trailing_30d_usd": "0",
    "per_pass_ceiling_usd": "7",
    "cap_30d_usd": "16",
    "verdict": "nothing_to_buy",
    "reasons": [],
}
FAMILIES = (("tick_tier", "tbbo"), ("bar_1m", "ohlcv-1m"), ("statistics", "statistics"))
BASELINE = {
    "health_exit": 0,
    "definitions": {"ES": 10, "GC": 5},
    "records": {f"{p}/{family}": 0 for p in ("ES", "GC") for family, _ in FAMILIES},
}


@pytest.fixture
def notes(fp: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fp, "NOTES_DIR", tmp_path)
    return tmp_path


# -- 5.2: the steps ------------------------------------------------------------


def test_a_failed_install_still_releases_the_timers(
    fp: ModuleType, cutover_error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    released: list[list[str]] = []

    def hold(held: list[str]) -> None:
        held.append("mt-tick.timer")

    def failing_install(*_: object) -> str:
        raise cutover_error("installer failed")

    monkeypatch.setattr(fp, "hold_timers", hold)
    monkeypatch.setattr(fp, "install", failing_install)
    monkeypatch.setattr(fp, "release_timers", lambda held: released.append(list(held)))
    with pytest.raises(cutover_error, match="installer failed"):
        fp.install_release("v0.27.0", "abc")
    assert released == [["mt-tick.timer"]]


def test_a_rerun_keeps_the_first_start_and_baselines(
    fp: ModuleType, ev: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def health() -> int:
        calls.append("h")
        return 0

    def definitions() -> dict[str, int]:
        calls.append("d")
        return {"ES": 1}

    monkeypatch.setattr(ev, "health_exit_code", health)
    monkeypatch.setattr(ev, "definition_counts", definitions)
    monkeypatch.setattr(ev, "record_totals", lambda: {"ES/bar_1m": 1})
    first = fp.record_start_and_baselines("304")
    second = fp.record_start_and_baselines("304")
    assert first == second
    assert calls == ["h", "d"]


def _drive(
    fp: ModuleType, ev: ModuleType, monkeypatch: pytest.MonkeyPatch, summary: Any
) -> list[str]:
    """Fake every host call of ``_steps``; return the calls made, in order."""
    calls: list[str] = []
    monkeypatch.setattr(fp, "preflight", lambda ref: "c" * 40)
    monkeypatch.setattr(fp, "read_env_text", lambda: ENV)
    monkeypatch.setattr(fp, "check_checkout_database", lambda checkout, prod: "tick")
    monkeypatch.setattr(fp, "read_checkout_env_text", lambda: "")
    monkeypatch.setattr(fp, "run_gates", lambda number: calls.append("gates"))
    monkeypatch.setattr(
        fp,
        "record_start_and_baselines",
        lambda slice_id: ("2026-11-08T00:00:00+00:00", BASELINE),
    )

    def note(what: str) -> None:
        calls.append(what)

    def tick(run_pass: object) -> int:
        note("tick")
        return 0

    def collect(*_: object) -> Any:
        note("collect")
        return ev.Evidence()

    monkeypatch.setattr(fp, "install_release", lambda ref, commit: note("install"))
    monkeypatch.setattr(fp, "fetch_estimate", lambda: summary)
    monkeypatch.setattr(fp, "run_passes", tick)
    monkeypatch.setattr(ev, "collect", collect)
    monkeypatch.setattr(ev, "health_exit_code", lambda: 0)
    return calls


def test_an_estimate_refusal_never_buys(
    fp: ModuleType,
    ev: ModuleType,
    cutover_error: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = NOTHING | {"verdict": "refused", "reasons": ["over"]}
    calls = _drive(fp, ev, monkeypatch, refused)
    with pytest.raises(cutover_error):
        fp._steps("v0.27.0", "304", 69)
    assert "tick" not in calls


def test_nothing_to_buy_skips_the_purchase_and_goes_to_evidence(
    fp: ModuleType, ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _drive(fp, ev, monkeypatch, NOTHING)
    assert fp._steps("v0.27.0", "304", 69) == 0
    assert calls == ["gates", "install", "collect"]


@pytest.mark.parametrize(
    ("exits", "runs", "final"),
    [([6, 6, 0], 3, 0), ([0], 1, 0), ([3], 1, 3), ([6, 3], 2, 3)],
)
def test_pass_exits_zero_and_three_end_the_loop_and_six_runs_again(
    fp: ModuleType, exits: list[int], runs: int, final: int
) -> None:
    seq = iter(exits)
    count = [0]

    def run_pass() -> int:
        count[0] += 1
        return next(seq)

    assert fp.run_passes(run_pass) == final
    assert count[0] == runs


def test_the_last_of_five_sixes_raises(
    fp: ModuleType, cutover_error: type[Exception]
) -> None:
    count = [0]

    def run_pass() -> int:
        count[0] += 1
        return 6

    with pytest.raises(cutover_error, match="jobs still processing after 5 runs"):
        fp.run_passes(run_pass)
    assert count[0] == fp.PASS_ATTEMPTS == 5


@pytest.mark.parametrize("code", [1, 2, 4, 5])
def test_any_other_exit_stops_with_that_code(fp: ModuleType, code: int) -> None:
    with pytest.raises(fp.PassStopped) as exc:
        fp.run_passes(lambda: code)
    assert exc.value.exit_code == code


def test_main_returns_the_stopping_exit_code(
    fp: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stopped(ref: str, slice_id: str, gate_issue: int) -> int:
        raise fp.PassStopped(4)

    monkeypatch.setattr(fp, "_steps", stopped)
    assert fp.main(["x", "--slice", "304", "--gate-issue", "69", "v0.27.0"]) == 4


def test_no_argument_prints_usage_and_exits_two(fp: ModuleType) -> None:
    assert fp.main(["x"]) == 2


def test_a_missing_slice_is_refused_and_nothing_runs(
    fp: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(ref: str, slice_id: str, gate_issue: int) -> int:
        raise AssertionError("_steps must not run")

    monkeypatch.setattr(fp, "_steps", never)
    assert fp.main(["x", "v0.27.0"]) == 2
    assert fp.main(["x", "--slice", " ", "v0.27.0"]) == 2
    assert list(notes.iterdir()) == []


def test_slice_304_keeps_304s_file_names(fp: ModuleType, notes: Path) -> None:
    assert fp.start_file("304") == notes / "304-cutover-start.txt"
    assert fp.baseline_file("304") == notes / "304-cutover-baseline.json"


def test_slice_307_names_its_own_state_files_and_log(
    fp: ModuleType, ev: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "health_exit_code", lambda: 0)
    monkeypatch.setattr(ev, "definition_counts", lambda: {"ES": 1})
    monkeypatch.setattr(ev, "record_totals", lambda: {"ES/bar_1m": 1})
    start, _ = fp.record_start_and_baselines("307")
    assert (notes / "307-cutover-start.txt").read_text().strip() == start
    assert (notes / "307-cutover-baseline.json").exists()
    assert fp.record_start_and_baselines("307")[0] == start  # re-run reads it back
    seen: list[str] = []

    def log(path: Path) -> Path:
        seen.append(path.name)
        return path

    monkeypatch.setattr(fp, "start_log", log)
    monkeypatch.setattr(fp, "_steps", lambda ref, slice_id, gate_issue: 0)
    assert fp.main(["x", "--slice", "307", "--gate-issue", "69", "v0.28.0"]) == 0
    assert seen[0].startswith("cutover-307-v0.28.0-")
    assert seen[0].endswith(".log")


# -- 5.4: evidence -------------------------------------------------------------


def _session(
    date: str, status: str, ledger: int, raw: int, check: str
) -> dict[str, Any]:
    return {
        "session_date": date,
        "status": status,
        "ledger": ledger,
        "raw": raw,
        "check": check,
        "unit_ids": [],
    }


def _coverage(*sessions: dict[str, Any]) -> dict[str, Any]:
    return {"products": [{"product": "ES", "sessions": list(sessions)}]}


def test_a_ledger_unequal_complete_session_fails_naming_it(ev: ModuleType) -> None:
    doc = _coverage(_session("2024-01-02", "complete", 5, 6, "mismatch"))
    failures = ev.ledger_failures(doc, "statistics")
    assert len(failures) == 1
    assert "2024-01-02" in failures[0]


def test_a_session_that_did_not_ingest_is_listed_not_failed(ev: ModuleType) -> None:
    doc = _coverage(
        _session("2024-01-02", "pending", 0, 0, "ok"),
        _session("2024-01-03", "complete", 4, 4, "ok"),
    )
    assert ev.ledger_failures(doc, "statistics") == []
    assert dict(ev.unfinished_sessions(doc)) == {"pending": 1}


def test_the_definition_delta_is_against_the_baseline(ev: ModuleType) -> None:
    after = {"ES": 40, "GC": 5}
    assert ev.definition_delta(BASELINE["definitions"], after) == {
        "ES": (10, 40, 30),
        "GC": (5, 5, 0),
    }


UNIT = {
    "unit_id": 0,
    "request_id": 1,
    "state": "ingested",
    "fetch_status": "UNKNOWN",
    "failure_reason": None,
}


def _accounting(*, caught_up: bool = True, over: int = 0) -> dict[str, Any]:
    """The real ``mt data accounting --json`` shape for ES and GC."""
    af = importlib.import_module("manta_trading.cli.commands.accounting_futures")
    ta = importlib.import_module("manta_trading.data.tick.tick_accounting")
    ct = importlib.import_module("manta_trading.data.tick.cross_tier_result")
    sf = importlib.import_module("manta_trading.data.tick.schema_families")
    complete = 10 if caught_up else 8
    volume = (
        ct.CrossTierResult("traded_vs_cleared", sf.SchemaFamily.BAR_1M, 9, 0, over, 0),
    )
    products = []
    for product in ("ES", "GC"):
        lines = [
            ta.TickAccountingLine(
                product, family, schema, 10, complete, {"complete": complete}, 0,
                caught_up, None, False, volume if family == "bar_1m" else (),
            )
            for family, schema in FAMILIES
        ]  # fmt: skip
        products.append(af._product_payload(product, lines))
    return {"rows": [], "tick": {"products": products}}


def _fake_mt_run(
    ev: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    units: list[dict[str, Any]],
    *,
    fail: str | None = None,
    sessions: list[dict[str, Any]] | dict[str, list[dict[str, Any]]] | None = None,
    accounting: dict[str, Any] | None = None,
    requests: list[dict[str, Any]] | None = None,
) -> None:
    def fake(args: list[str]) -> subprocess.CompletedProcess[str]:
        joined = " ".join(args)
        if fail and fail in joined:
            return subprocess.CompletedProcess(args, 4, "", "storage down")
        if "accounting" in joined:
            body: Any = accounting or _accounting()
        elif "coverage" in joined:
            chosen = sessions or []
            if isinstance(chosen, dict):
                chosen = next((v for k, v in chosen.items() if k in joined), [])
            body = _coverage(*chosen)
        elif "debug requests" in joined:
            body = {"rows": requests or []}
        elif "units" in joined:
            body = {"rows": units}
        elif "overview" in joined:
            body = {"passes": []}
        else:
            body = {"rows": [{}, {}], "exit_code": 0}
        return subprocess.CompletedProcess(args, 0, json.dumps(body), "")

    monkeypatch.setattr(ev, "mt_run", fake)
    monkeypatch.setattr(ev, "_reconcile", lambda *a: None)
    monkeypatch.setattr(ev, "run_session_check", lambda dirs, calendar: "pass")


def test_a_failing_evidence_command_stops_the_script_naming_it(
    ev: ModuleType, cutover_error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_mt_run(ev, monkeypatch, [], fail="debug units")
    with pytest.raises(cutover_error, match="debug units"):
        ev.collect("2026-11-08T00:00:00+00:00", D(7), BASELINE)


# -- 5.6: the exit rule --------------------------------------------------------


def _settled(ev: ModuleType, monkeypatch: pytest.MonkeyPatch, health: int = 0) -> None:
    monkeypatch.setattr(ev, "health_exit_code", lambda: health)


def test_a_changed_health_exit_fails(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch, health=1)
    assert ev.final_failures(ev.Evidence(), BASELINE, NOTHING)


def test_a_second_estimate_that_plans_a_request_fails(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch)
    planned = NOTHING | {"verdict": "allowed", "planned_usd": "1.2"}
    assert ev.final_failures(ev.Evidence(), BASELINE, planned)


def test_a_failed_reconciliation_fails(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch)
    found = ev.Evidence(failures=["reconciliation failed"])
    assert ev.final_failures(found, BASELINE, NOTHING) == ["reconciliation failed"]


def test_a_unit_with_no_reason_that_is_not_ingested_fails(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch)
    units = [{**UNIT, "unit_id": 7, "state": "submitted"}]
    _fake_mt_run(ev, monkeypatch, units)
    found = ev.collect("2026-11-08T00:00:00+00:00", D(7), BASELINE)
    assert found.unfinal_units == [7]
    assert ev.final_failures(found, BASELINE, NOTHING)


def test_failed_units_with_reasons_and_coverage_findings_pass(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch)
    units = [
        {**UNIT, "unit_id": 1, "state": "ingested"},
        {
            **UNIT,
            "unit_id": 2,
            "state": "verified",
            "failure_reason": "stat_reference: instrument_id 1: not a session",
        },
    ]
    sessions = [_session("2024-01-02", "retry_exhausted", 0, 0, "ok")]
    _fake_mt_run(ev, monkeypatch, units, sessions=sessions)
    found = ev.collect("2026-11-08T00:00:00+00:00", D(7), BASELINE)
    assert ev.final_failures(found, BASELINE, NOTHING) == []


# -- slice 307 1.1: reads and placement derive from the universe ----------------


def test_coverage_reads_derive_from_the_universe(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    universe = _load("cutover_304_checks").TICK_UNIVERSE
    entry = universe[0].__class__(
        "ES",
        ("ES.FUT",),
        universe[0].stype_in,
        tier=universe[0].tier,
        start=universe[0].start,
        end=universe[0].end,
        history=(
            universe[0]
            .history[0]
            .__class__(ev.TickSchema.DEFINITION, date(2020, 1, 1), date(2020, 3, 1)),
            universe[0]
            .history[0]
            .__class__(ev.TickSchema.OHLCV_1M, date(2020, 1, 1), date(2020, 2, 1)),
        ),
    )
    monkeypatch.setattr(ev, "TICK_UNIVERSE", (entry,))
    assert ev.coverage_reads() == [("ES", "ohlcv-1m", "2020-01-01", "2020-02-01")]


def test_an_open_ended_want_is_a_stop_not_a_default(
    ev: ModuleType, cutover_error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    base = _load("cutover_304_checks").TICK_UNIVERSE[0]
    open_want = base.history[0].__class__(ev.TickSchema.STATISTICS, date(2020, 1, 1))
    entry = base.__class__(
        "ES",
        ("ES.FUT",),
        base.stype_in,
        tier=base.tier,
        start=base.start,
        end=base.end,
        history=(open_want,),
    )
    monkeypatch.setattr(ev, "TICK_UNIVERSE", (entry,))
    with pytest.raises(cutover_error, match="no end date"):
        ev.coverage_reads()


def test_session_placement_runs_once_per_product_with_its_calendar(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, list[Path]]] = []

    def check(dirs: list[Path], calendar: str) -> str:
        calls.append((calendar, list(dirs)))
        return "pass"

    monkeypatch.setattr(ev, "run_session_check", check)
    rows = [
        {"schema": "ohlcv-1m", "provider_job_id": "J-ES", "symbols": "ES.FUT"},
        {"schema": "ohlcv-1m", "provider_job_id": "J-GC", "symbols": "GC.FUT"},
    ]
    ev._session_placement(rows)
    constants = _load("cutover_304_checks")
    assert [c for c, _ in calls] == [
        constants.calendar_for("ES"),
        constants.calendar_for("GC"),
    ]
    assert calls[0][1] != calls[1][1]


# -- slice 307 1.7: --estimate -------------------------------------------------


def _drive_estimate(
    fp: ModuleType, monkeypatch: pytest.MonkeyPatch, summary: Any, env: str
) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(fp, "preflight", lambda ref: "c" * 40)
    monkeypatch.setattr(fp, "read_env_text", lambda: env)
    monkeypatch.setattr(fp, "check_checkout_database", lambda checkout, prod: "tick")
    monkeypatch.setattr(fp, "read_checkout_env_text", lambda: "")
    monkeypatch.setattr(fp, "fetch_estimate", lambda: summary)
    monkeypatch.setattr(fp, "run_gates", lambda number: None)
    for name in ("install_release", "run_passes", "record_start_and_baselines"):
        monkeypatch.setattr(fp, name, lambda *a, _n=name: calls.append(_n))
    return calls


@pytest.mark.parametrize("env", ["", "MT_TICK_SPEND_CEILING_USD=1\n", ENV])
def test_estimate_prints_the_command_and_buys_nothing(
    fp: ModuleType,
    notes: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: str,
) -> None:
    summary = NOTHING | {
        "verdict": "refused",
        "reasons": ["ceiling"],
        "per_pass_ceiling_usd": None,
        "cap_30d_usd": None,
        "planned_usd": "89",
        "requests": [
            {
                "schema": "ohlcv-1m",
                "start": "2010-06-08",
                "end": "2010-07-01",
                "resubmit": False,
            }
        ],
    }
    calls = _drive_estimate(fp, monkeypatch, summary, env)

    def tick_once() -> int:
        calls.append("tick")
        return 0

    monkeypatch.setattr(fp, "_tick_once", tick_once)
    assert (
        fp.main(["x", "--slice", "307", "--gate-issue", "69", "--estimate", "v0.28.0"])
        == 0
    )
    assert "--per-pass 90 --cap-30d 90" in capsys.readouterr().out
    assert calls == []  # no install, no pass, no start or baseline recorded
    assert [p.name for p in notes.iterdir() if "cutover-307" not in p.name] == []


def test_estimate_still_refuses_a_set_plan(
    fp: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _drive_estimate(fp, monkeypatch, NOTHING, "MT_TICK_DATABENTO_PLAN=standard\n")
    assert (
        fp.main(["x", "--slice", "307", "--gate-issue", "69", "--estimate", "v0.28.0"])
        == 1
    )


def test_the_full_cutover_still_stops_on_an_unset_ceiling(
    fp: ModuleType, ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _drive(fp, ev, monkeypatch, NOTHING)
    monkeypatch.setattr(fp, "read_env_text", lambda: "")
    with pytest.raises(_load("cutover_common").CutoverError, match="not set"):
        fp._steps("v0.28.0", "307", 69)


# -- slice 307 2.3: the gates in the preflight ---------------------------------


def _gated(
    fp: ModuleType,
    ev: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    issue: str = "CLOSED",
    edge: str = "2099-01-01T00:00:00+00:00",
) -> list[str]:
    """``_drive`` with the real gates over fakes for ``gh`` and ``status``."""
    real_gates = fp.run_gates  # read before ``_drive`` replaces it with a fake
    calls = _drive(fp, ev, monkeypatch, NOTHING)
    monkeypatch.setattr(fp, "run_gates", real_gates)
    monkeypatch.setattr(fp, "_view_issue", lambda n: json.dumps({"state": issue}))
    body = json.dumps({"products": [{"product": "ES", "edge_available_end": edge}]})
    monkeypatch.setattr(
        ev, "mt_run", lambda args: subprocess.CompletedProcess(args, 0, body, "")
    )
    return calls


def test_a_missing_gate_issue_is_refused_with_exit_two(
    fp: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(ref: str, slice_id: str, gate_issue: int) -> int:
        raise AssertionError("_steps must not run")

    monkeypatch.setattr(fp, "_steps", never)
    assert fp.main(["x", "--slice", "307", "v0.28.0"]) == 2
    assert fp.main(["x", "--slice", "307", "--gate-issue", "abc", "v0.28.0"]) == 2


def test_an_open_gate_issue_stops_before_the_install_and_writes_nothing(
    fp: ModuleType,
    ev: ModuleType,
    cutover_error: type[Exception],
    notes: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _gated(fp, ev, monkeypatch, issue="OPEN")
    with pytest.raises(cutover_error, match="invoice comparison"):
        fp._steps("v0.28.0", "307", 69)
    assert "install" not in calls
    assert list(notes.iterdir()) == []


def test_a_short_dataset_edge_stops_before_the_install_and_writes_nothing(
    fp: ModuleType,
    ev: ModuleType,
    cutover_error: type[Exception],
    notes: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _gated(fp, ev, monkeypatch, edge="2010-01-01T00:00:00+00:00")
    with pytest.raises(cutover_error, match="before the latest want end"):
        fp._steps("v0.28.0", "307", 69)
    assert "install" not in calls
    assert list(notes.iterdir()) == []


def test_closed_issue_and_sufficient_edge_let_the_cutover_go_on(
    fp: ModuleType, ev: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _gated(fp, ev, monkeypatch)
    assert fp._steps("v0.28.0", "307", 69) == 0
    assert calls[0] == "install"


# -- slice 307 4.8: the D6 evidence items and the exit rule -----------------------

START = "2026-11-08T00:00:00+00:00"
ES_STATS = {
    "statistics": [
        _session("2026-11-27", "complete", 4, 4, "ok"),
        _session("2026-11-30", "missing", 0, 0, "ok"),
    ]
}


def _run(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> tuple[Any, list[str]]:
    _settled(ev, monkeypatch)
    units = kwargs.pop("units", [{**UNIT, "unit_id": 1}])
    _fake_mt_run(ev, monkeypatch, units, **kwargs)
    found = ev.collect(START, D(7), BASELINE)
    return found, ev.final_failures(found, BASELINE, NOTHING)


def test_an_unaccounted_for_session_fails_the_exit_rule(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = {
        "statistics": [
            _session("2026-11-27", "pending", 0, 0, "ok"),
            _session("2026-11-30", "complete", 4, 4, "ok"),
        ]
    }
    _, failures = _run(
        ev, monkeypatch, sessions=sessions, accounting=_accounting(caught_up=False)
    )
    assert any("2026-11-27 pending is not accounted for" in f for f in failures)


def test_a_named_publication_after_want_end_session_does_not_fail(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    found, failures = _run(
        ev, monkeypatch, sessions=ES_STATS, accounting=_accounting(caught_up=False)
    )
    assert failures == []
    assert found.failures == []


def test_a_failed_retryable_request_fails_the_exit_rule(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    units = [{**UNIT, "unit_id": 2, "fetch_status": "FAILED_RETRYABLE"}]
    _, failures = _run(ev, monkeypatch, units=units)
    assert any("request 1 is still failed-retryable" in f for f in failures)


def test_a_retry_exhausted_request_is_listed_and_its_units_are_final(
    ev: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = {
        "request_id": 1,
        "schema": "ohlcv-1m",
        "symbols": "ES.FUT",
        "range_start": "2026-11-01",
        "range_end": "2026-12-01",
        "provider_job_id": None,
        "requested_at": START,
    }
    units = [
        {
            **UNIT,
            "unit_id": 3,
            "state": "requested",
            "fetch_status": "RETRY_EXHAUSTED",
            "failure_reason": None,
        }
    ]
    found, failures = _run(ev, monkeypatch, units=units, requests=[request])
    assert found.unfinal_units == []
    assert failures == []
    printed = capsys.readouterr().out
    assert "acquisition failed" in printed
    assert "(the manifest holds no error text)" in printed


def test_an_over_session_fails_the_exit_rule(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, failures = _run(ev, monkeypatch, accounting=_accounting(over=2))
    assert any("2 sessions over in traded_vs_cleared" in f for f in failures)


def test_a_clean_run_has_no_failures_and_narrates_the_family_rows(
    ev: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    found, failures = _run(ev, monkeypatch)
    assert failures == []
    out = capsys.readouterr().out
    assert "ES ohlcv-1m: sessions 10/10" in out
    assert "ledger records (before, after, delta)" in out


def test_a_baseline_with_no_record_totals_is_a_stop(
    ev: ModuleType, cutover_error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_mt_run(ev, monkeypatch, [])
    old = {k: v for k, v in BASELINE.items() if k != "records"}
    with pytest.raises(cutover_error, match="predates 307"):
        ev.collect(START, D(7), old)
