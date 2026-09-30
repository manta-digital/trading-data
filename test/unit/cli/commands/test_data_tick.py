"""``mt data tick estimate``: output and the three exit codes (slice 220).

The provider is a real ``DatabentoTickProvider`` over a fake ``Historical``
(``batch`` and ``timeseries`` raise on access); settings are real, built with
``_env_file=None`` so no test reads ``.env``. No network, no key.
"""

from __future__ import annotations

import errno
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import httpx
import psycopg
import pytest
from click.exceptions import UsageError
from tick_support.fake_historical import FakeApi, FakeHistorical, server_error
from tick_support.metadata_responses import bundle, metadata_api, symbology_api
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import tick as cmd
from manta_trading.cli.commands.tick_render import VERDICT_LABELS, human_bytes, usd
from manta_trading.config import Settings
from manta_trading.data.tick import acquisition_pass, adopt, reset, run_context
from manta_trading.data.tick.adopt import (
    AdoptResult,
    TickVerifyInterrupted,
)
from manta_trading.data.tick.adopt_files import (
    ArchivedFile,
    TickAdoptionRefused,
    TickArchiveWriteError,
)
from manta_trading.data.tick.constants import (
    ESTIMATE_SCHEMAS,
    TICK_SPEND_CEILING_ENV,
    TickSchema,
)
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.estimate import CeilingVerdict
from manta_trading.data.tick.pass_contract import (
    ACQUISITION_PHASE_NAMES,
    PassResult,
    PhaseReport,
    TickOutcome,
    TickPassPhaseName,
)
from manta_trading.data.tick.pass_contract import (
    SKIPPED as PASS_SKIPPED,
)
from manta_trading.data.tick.reset import ResetAction, ResetChange
from manta_trading.data.tick.run_context import TickPreflightError
from manta_trading.data.tick.tick_calendar import TickCalendarError
from manta_trading.providers.errors import ProviderTransientError

runner = CliRunner()
ESTIMATE = ["data", "tick", "estimate", "--symbols", "ES.c.0", "--stype", "continuous"]
RANGE = ["--start", "2025-01-06", "--end", "2025-01-11"]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Environment-only settings: a key, no ceiling, no SDK fallback key."""
    monkeypatch.setenv("MT_DATABENTO_API_KEY", "db-test-key")
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    monkeypatch.delenv(TICK_SPEND_CEILING_ENV, raising=False)
    with patch(
        "manta_trading.cli.app.Settings", side_effect=lambda: Settings(_env_file=None)
    ):
        yield monkeypatch


@pytest.fixture
def metadata(env: pytest.MonkeyPatch) -> Iterator[FakeApi]:
    """Route ``from_settings`` to a provider over the fake ``Historical``."""
    api = metadata_api()

    def build(settings: Settings) -> DatabentoTickProvider:
        assert settings.databento_api_key == "db-test-key"
        client = FakeHistorical(metadata=api, symbology=symbology_api())
        http = httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(599))
        )
        return DatabentoTickProvider(client, http)  # type: ignore[arg-type]

    with patch.object(DatabentoTickProvider, "from_settings", side_effect=build):
        yield api


def test_happy_path_prints_four_rows(metadata: FakeApi) -> None:
    result = runner.invoke(app, [*ESTIMATE, *RANGE], env={"COLUMNS": "200"})
    assert result.exit_code == cmd.EXIT_OK, result.output
    for schema in ESTIMATE_SCHEMAS:
        assert schema.value in result.output
    assert "not purchasable in this initiative" in result.output
    assert "bought with each tier" in result.output
    assert f"{TICK_SPEND_CEILING_ENV} unset" in result.output
    assert "end exclusive" in result.output
    assert "5 available" in result.output


def test_json_carries_the_ceiling_fields(
    metadata: FakeApi, env: pytest.MonkeyPatch
) -> None:
    env.setenv(TICK_SPEND_CEILING_ENV, "4.00")
    result = runner.invoke(app, [*ESTIMATE, *RANGE, "--json"])
    assert result.exit_code == cmd.EXIT_OK, result.output
    payload = json.loads(result.stdout)
    assert payload["ceiling_usd"] == "4.00"
    rows = {row["schema"]: row for row in payload["schemas"]}
    assert set(rows) == {s.value for s in ESTIMATE_SCHEMAS}
    assert rows["trades"]["ceiling_verdict"] == "within"
    assert rows["tbbo"]["ceiling_verdict"] == "over"
    assert rows["tbbo"]["bundle_cost_usd"] == str(bundle(TickSchema.TBBO))


def test_missing_key_exits_preflight_naming_the_variable(
    env: pytest.MonkeyPatch,
) -> None:
    env.delenv("MT_DATABENTO_API_KEY")
    result = runner.invoke(app, [*ESTIMATE, *RANGE])
    assert result.exit_code == cmd.EXIT_PREFLIGHT
    assert "MT_DATABENTO_API_KEY" in result.output


@pytest.mark.parametrize("end", ["2025-01-06", "2025-01-05"])
def test_end_not_after_start_exits_preflight(metadata: FakeApi, end: str) -> None:
    result = runner.invoke(app, [*ESTIMATE, "--start", "2025-01-06", "--end", end])
    assert result.exit_code == cmd.EXIT_PREFLIGHT
    assert "exclusive" in result.output
    assert metadata.calls == []


def test_end_past_the_edge_exits_preflight_naming_it(metadata: FakeApi) -> None:
    result = runner.invoke(
        app, [*ESTIMATE, "--start", "2026-09-20", "--end", "2030-01-01"]
    )
    assert result.exit_code == cmd.EXIT_PREFLIGHT
    assert "2026-09-27T12:24:28" in result.output


def test_provider_transient_exits_provider(metadata: FakeApi) -> None:
    metadata.fail("get_cost", server_error(503))
    result = runner.invoke(app, [*ESTIMATE, *RANGE])
    assert result.exit_code == cmd.EXIT_PROVIDER
    assert "provider error" in result.output


def test_stype_outside_the_choices_is_a_usage_error(env: pytest.MonkeyPatch) -> None:
    """Click's own usage error: exit 2, the collision the epilog states."""
    args = ["data", "tick", "estimate", "--symbols", "ES", "--stype", "bogus", *RANGE]
    result = runner.invoke(app, args)
    assert result.exit_code == 2


def test_group_lists_its_verbs() -> None:
    names = [c.name for c in cmd.tick_app.registered_commands]
    assert names == ["estimate", "adopt", "reset", "pass"]


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (96_000_000, "91.6 MiB"),
        (4_800_000_000, "4.5 GiB"),
        (2 * 1024**5, "2048.0 TiB"),
    ],
)
def test_human_bytes(size: int, text: str) -> None:
    assert human_bytes(size) == text


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        ("127.46987760067", "$127.47"),
        ("0.000004116446", "<$0.01"),
        ("0", "$0.00"),
        ("1234.5", "$1,234.50"),
        ("0.015", "$0.02"),
    ],
)
def test_usd_rounds_to_cents(amount: str, text: str) -> None:
    assert usd(Decimal(amount)) == text


def test_json_verdicts_are_tokens(metadata: FakeApi) -> None:
    """``--json`` carries machine tokens; the table carries the wording."""
    result = runner.invoke(app, [*ESTIMATE, *RANGE, "--json"])
    verdicts = {r["ceiling_verdict"] for r in json.loads(result.stdout)["schemas"]}
    assert verdicts == {"no_ceiling", "not_purchasable", "bought_with_each_tier"}


def test_every_verdict_has_operator_wording() -> None:
    assert set(VERDICT_LABELS) == set(CeilingVerdict)
    assert (
        f"{TICK_SPEND_CEILING_ENV} unset" in VERDICT_LABELS[CeilingVerdict.NO_CEILING]
    )


# -- adopt and reset (slice 223) -----------------------------------------------


class _Run:
    """What the patched ``open_tick_run`` yields; the cores are patched too."""


@asynccontextmanager
async def _fake_run(settings: Settings) -> AsyncIterator[_Run]:
    yield _Run()


def _raising_run(exc: BaseException) -> Any:
    @asynccontextmanager
    async def fake(settings: Settings) -> AsyncIterator[_Run]:
        raise exc
        yield _Run()  # pragma: no cover - makes this an async generator

    return fake


ADOPT = ["data", "tick", "adopt", "--job-id", "GLBX-TEST", "--source", "/tmp/job"]
ADOPTED = AdoptResult(
    job_id="GLBX-TEST",
    already_adopted=False,
    cost_usd=Decimal("12.5785"),
    files=(ArchivedFile("glbx-mdp3-20240903.trades.dbn.zst", 10, "ab", True),),
    units_by_state={"verified": 1},
    holes=(date(2024, 9, 9),),
    strays=(),
)


@pytest.fixture
def writer_run(env: pytest.MonkeyPatch) -> Iterator[None]:
    with patch.object(run_context, "open_tick_run", side_effect=_fake_run):
        yield


def _adopt_returning(result: Any) -> Any:
    async def core(*args: Any) -> Any:
        if isinstance(result, BaseException):
            raise result
        return result

    return patch.object(adopt, "adopt_job", side_effect=core)


def test_adopt_json_shape(writer_run: None) -> None:
    with _adopt_returning(ADOPTED):
        result = runner.invoke(app, [*ADOPT, "--json"])
    assert result.exit_code == cmd.EXIT_OK, result.output
    body = json.loads(result.stdout)
    assert body["cost_usd"] == "12.5785"
    assert body["units_by_state"] == {"verified": 1}
    assert body["holes"] == ["2024-09-09"]
    assert body["files"][0]["copied"] is True


def test_adopt_text_names_holes(writer_run: None) -> None:
    with _adopt_returning(ADOPTED):
        result = runner.invoke(app, ADOPT, env={"COLUMNS": "200"})
    assert result.exit_code == cmd.EXIT_OK, result.output
    assert "Holes: 2024-09-09" in result.stdout


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (TickAdoptionRefused("refused"), cmd.EXIT_PREFLIGHT),
        (ProviderTransientError("down"), cmd.EXIT_PROVIDER),
        (
            TickArchiveWriteError(Path("/a/b"), OSError(errno.ENOSPC, "full")),
            cmd.EXIT_STORAGE,
        ),
        (TickCalendarError("calendar CME_EQUITY unavailable"), cmd.EXIT_STORAGE),
        (psycopg.OperationalError("gone"), cmd.EXIT_STORAGE),
        (TickVerifyInterrupted("run it again"), cmd.EXIT_PROVIDER),
    ],
)
def test_adopt_exit_codes(writer_run: None, exc: BaseException, code: int) -> None:
    with _adopt_returning(exc):
        result = runner.invoke(app, [*ADOPT, "--json"])
    assert result.exit_code == code, result.output
    assert json.loads(result.stderr)["error"] == str(exc)


@pytest.mark.parametrize("already_adopted", [False, True])
def test_adopt_with_failed_units_exits_3(
    writer_run: None, already_adopted: bool
) -> None:
    failed = replace(
        ADOPTED,
        already_adopted=already_adopted,
        reverified=1,
        verify_failures=("2024-09-03: size 9, recorded 10",),
    )
    with _adopt_returning(failed):
        result = runner.invoke(app, ADOPT, env={"COLUMNS": "200"})
    assert result.exit_code == cmd.EXIT_PARTIAL, result.output
    assert "Verification failed: 2024-09-03" in result.stdout


def test_readopt_names_the_units_it_verified(writer_run: None) -> None:
    resumed = AdoptResult("GLBX-TEST", already_adopted=True, reverified=2)
    with _adopt_returning(resumed):
        result = runner.invoke(app, ADOPT)
    assert result.exit_code == cmd.EXIT_OK, result.output
    assert "verified the 2 unit(s) left unverified" in result.stdout


def test_preflight_refusal_exits_1(env: pytest.MonkeyPatch) -> None:
    refusal = TickPreflightError("MT_TICK_ARCHIVE_DIR is not set")
    with patch.object(run_context, "open_tick_run", side_effect=_raising_run(refusal)):
        result = runner.invoke(app, [*ADOPT, "--json"])
    assert result.exit_code == cmd.EXIT_PREFLIGHT
    assert "MT_TICK_ARCHIVE_DIR" in result.stderr


RESET = ["data", "tick", "reset"]


def _reset_returning(changes: list[ResetChange]) -> Any:
    async def core(run: Any, targets: Any) -> list[ResetChange]:
        return changes

    return patch.object(reset, "reset_units", side_effect=core)


def test_reset_json_shape(writer_run: None) -> None:
    changes = [ResetChange(9, ResetAction.NOT_FOUND, None, None)]
    with _reset_returning(changes) as core:
        result = runner.invoke(app, [*RESET, "--unit-id", "9", "--json"])
    assert result.exit_code == cmd.EXIT_OK, result.output
    [change] = json.loads(result.stdout)["changes"]
    assert change == {
        "unit_id": 9,
        "action": "not_found",
        "before": None,
        "after": None,
    }
    assert core.call_args.args[1] == [9]


def test_reset_refuses_without_the_typed_word(writer_run: None) -> None:
    with _reset_returning([]) as core:
        result = runner.invoke(app, [*RESET, "--all"], input="yes\n")
    assert result.exit_code == cmd.EXIT_PREFLIGHT
    core.assert_not_called()


def test_reset_proceeds_on_the_typed_word(writer_run: None) -> None:
    with _reset_returning([]) as core:
        result = runner.invoke(app, [*RESET, "--all"], input="reset\n")
    assert result.exit_code == cmd.EXIT_OK, result.output
    assert core.call_args.args[1] == reset.ALL


@pytest.mark.parametrize("args", [["--all", "--unit-id", "3"], []])
def test_reset_needs_exactly_one_target(writer_run: None, args: list[str]) -> None:
    with _reset_returning([]) as core:
        result = runner.invoke(app, [*RESET, *args, "--yes"])
    assert result.exit_code == cmd.EXIT_PREFLIGHT
    core.assert_not_called()


# -- exit codes for the pass (slice 224) -------------------------------------


def test_every_tick_outcome_has_an_exit_code() -> None:
    assert set(cmd.EXIT_BY_OUTCOME) == set(TickOutcome)


def test_every_delivery_counter_has_a_report_label() -> None:
    from manta_trading.cli.commands.tick_pass_render import _DELIVERY_LABELS
    from manta_trading.data.tick.in_flight import AdvanceTally

    assert set(_DELIVERY_LABELS) == set(AdvanceTally().counts())


def test_exit_codes_are_zero_to_six_and_unique() -> None:
    codes = list(cmd.EXIT_BY_OUTCOME.values())
    assert sorted(codes) == [0, 2, 3, 4, 5, 6]
    assert len(set(codes)) == len(codes)
    assert {cmd.EXIT_PREFLIGHT, *codes} == set(range(7))


def test_pass_exit_codes_reuse_223s_partial_and_storage() -> None:
    assert cmd.EXIT_BY_OUTCOME[TickOutcome.PARTIAL] == cmd.EXIT_PARTIAL == 3
    assert cmd.EXIT_BY_OUTCOME[TickOutcome.STORAGE_ABORT] == cmd.EXIT_STORAGE == 4
    assert cmd.EXIT_BY_OUTCOME[TickOutcome.REFUSED] == cmd.EXIT_REFUSED == 5
    assert cmd.EXIT_BY_OUTCOME[TickOutcome.IN_FLIGHT] == cmd.EXIT_IN_FLIGHT == 6


# -- the pass verb and its report (slice 224) ----------------------------------

PASS = ["data", "tick", "pass"]
STARTED = datetime(2026, 9, 29, 12, tzinfo=UTC)
PURCHASE_SUMMARY: dict[str, Any] = {
    "wanted_days": {"ES/definition": 4},
    "pending_days": {},
    "missing_days": {},
    "requests": [
        {
            "schema": "definition",
            "start": "2024-09-01",
            "end": "2024-09-30",
            "days": 4,
            "cost_usd": "0.0014",
            "resubmit": False,
        }
    ],
    "planned_usd": "0.0041",
    "trailing_30d_usd": "0",
    "unheld_jobs": [{"job_id": "GLBX-PORTAL", "cost_usd": "3.5"}],
    "per_pass_ceiling_usd": "0.50",
    "cap_30d_usd": None,
    "verdict": "refused",
    "reasons": ["absent: MT_TICK_SPEND_30D_CEILING_USD [x]"],
    "submitted": [],
}


def _report(
    name: TickPassPhaseName,
    outcome: TickOutcome | str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> PhaseReport:
    return PhaseReport(name, outcome, summary or {}, 12, error)  # type: ignore[arg-type]


def _result(
    outcome: TickOutcome, reports: tuple[PhaseReport, ...] | None = None
) -> PassResult:
    phases = reports or tuple(
        _report(name, TickOutcome.OK) for name in ACQUISITION_PHASE_NAMES
    )
    return PassResult(UUID(int=7), STARTED, phases, outcome, 345)


def _pass_returning(result: PassResult | BaseException) -> Any:
    calls: list[tuple[Any, ...]] = []

    async def core(*args: Any, **kwargs: Any) -> PassResult:
        calls.append(args)
        if isinstance(result, BaseException):
            raise result
        return result

    patcher = patch.object(acquisition_pass, "run_pass", side_effect=core)
    patcher.calls = calls  # type: ignore[attr-defined]
    return patcher


@pytest.mark.parametrize("outcome", list(TickOutcome))
def test_the_exit_code_follows_the_outcome(
    writer_run: None, outcome: TickOutcome
) -> None:
    with _pass_returning(_result(outcome)):
        result = runner.invoke(app, [*PASS, "--json"])
    assert result.exit_code == cmd.EXIT_BY_OUTCOME[outcome], result.output
    body = json.loads(result.stdout)
    assert body["outcome"] == outcome.value
    assert body["exit_code"] == cmd.EXIT_BY_OUTCOME[outcome]


def test_pass_json_shape(writer_run: None) -> None:
    reports = tuple(
        _report(name, TickOutcome.OK, {"n": 1}) for name in ACQUISITION_PHASE_NAMES
    )
    with _pass_returning(_result(TickOutcome.OK, reports)):
        result = runner.invoke(app, [*PASS, "--json"])
    body = json.loads(result.stdout)
    assert set(body) == {
        "run_id",
        "started_at",
        "phases",
        "outcome",
        "duration_ms",
        "exit_code",
    }
    assert [p["name"] for p in body["phases"]] == [
        n.value for n in ACQUISITION_PHASE_NAMES
    ]
    assert body["phases"][0]["summary"] == {"n": 1}
    assert body["run_id"] == str(UUID(int=7))


def test_pass_passes_the_window_and_estimate_flag_to_the_run(
    writer_run: None,
) -> None:
    core = _pass_returning(_result(TickOutcome.OK))
    with core:
        result = runner.invoke(
            app,
            [*PASS, "--start", "2024-09-01", "--end", "2024-10-01", "--estimate-only"],
        )
    assert result.exit_code == cmd.EXIT_OK, result.output
    ((_, window, estimate_only, _reader),) = core.calls
    assert window == (date(2024, 9, 1), date(2024, 10, 1))
    assert estimate_only is True


def test_pass_without_flags_is_unwindowed_and_buys(writer_run: None) -> None:
    core = _pass_returning(_result(TickOutcome.OK))
    with core:
        runner.invoke(app, PASS)
    ((_, window, estimate_only, _reader),) = core.calls
    assert window == (None, None) and estimate_only is False


@pytest.mark.parametrize(
    "args",
    [
        ["--start", "2024-10-01", "--end", "2024-10-01"],
        ["--start", "2024-10-02", "--end", "2024-10-01"],
    ],
)
def test_an_end_not_after_start_exits_preflight_without_running(
    writer_run: None, args: list[str]
) -> None:
    core = _pass_returning(_result(TickOutcome.OK))
    with core:
        result = runner.invoke(app, [*PASS, *args])
    assert result.exit_code == cmd.EXIT_PREFLIGHT
    assert core.calls == []


def test_a_bad_date_is_a_usage_error(writer_run: None) -> None:
    result = runner.invoke(app, [*PASS, "--start", "09/01/2024"])
    assert result.exit_code == UsageError.exit_code


def test_a_provider_error_out_of_the_run_exits_provider(writer_run: None) -> None:
    with _pass_returning(ProviderTransientError("down")):
        result = runner.invoke(app, PASS)
    assert result.exit_code == cmd.EXIT_PROVIDER


def test_a_preflight_refusal_exits_preflight(env: pytest.MonkeyPatch) -> None:
    refusal = TickPreflightError("MT_TICK_ARCHIVE_DIR is not set")
    with patch.object(run_context, "open_tick_run", side_effect=_raising_run(refusal)):
        result = runner.invoke(app, PASS)
    assert result.exit_code == cmd.EXIT_PREFLIGHT
    assert "MT_TICK_ARCHIVE_DIR" in result.output


def test_the_text_report_shows_phases_the_plan_and_the_closing_line(
    writer_run: None,
) -> None:
    reports = (
        _report(TickPassPhaseName.RECONCILE, TickOutcome.OK, {"polled": 1}),
        _report(TickPassPhaseName.AVAILABILITY, TickOutcome.OK, {}),
        _report(
            TickPassPhaseName.PURCHASE,
            TickOutcome.REFUSED,
            PURCHASE_SUMMARY,
            error=None,
        ),
        _report(TickPassPhaseName.AWAIT, PASS_SKIPPED),
        _report(TickPassPhaseName.DEFINITIONS, PASS_SKIPPED),
    )
    with _pass_returning(_result(TickOutcome.REFUSED, reports)):
        result = runner.invoke(app, PASS)
    assert result.exit_code == cmd.EXIT_REFUSED
    text = result.stdout
    for name in ACQUISITION_PHASE_NAMES:
        assert str(name) in text
    assert "jobs polled: 1" in text
    assert "$0.0041" in text and "$0.0014" in text  # sub-cent amounts stay visible
    assert "GLBX-PORTAL" in text and "verdict: refused" in text
    assert "30-day cap unset" in text
    assert "MT_TICK_SPEND_30D_CEILING_USD [x]" in text  # brackets are not markup
    assert "Outcome: refused  exit 5" in text
    assert text.count("await") == 1  # a skipped phase gets a table row, no section


def test_the_report_lists_jobs_in_flight_with_their_deadlines(
    writer_run: None,
) -> None:
    summary = {
        "waited_seconds": 1800,
        "in_flight": [
            {"job_id": "GLBX-SLOW", "state": "processing", "deadline": "2026-10-29"}
        ],
    }
    reports = (
        *(_report(n, TickOutcome.OK) for n in list(TickPassPhaseName)[:3]),
        _report(TickPassPhaseName.AWAIT, TickOutcome.IN_FLIGHT, summary),
        _report(TickPassPhaseName.DEFINITIONS, TickOutcome.OK),
    )
    with _pass_returning(_result(TickOutcome.IN_FLIGHT, reports)):
        result = runner.invoke(app, PASS)
    assert result.exit_code == cmd.EXIT_IN_FLIGHT
    assert "in flight: GLBX-SLOW (processing), deadline 2026-10-29" in result.stdout
    assert "seconds waited: 1800" in result.stdout
