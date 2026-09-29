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
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import psycopg
import pytest
from tick_support.fake_historical import FakeApi, FakeHistorical, server_error
from tick_support.metadata_responses import bundle, metadata_api, symbology_api
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import tick as cmd
from manta_trading.cli.commands.tick_render import VERDICT_LABELS, human_bytes, usd
from manta_trading.config import Settings
from manta_trading.data.tick import adopt, reset, run_context
from manta_trading.data.tick.adopt import AdoptResult, TickCalendarError
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
from manta_trading.data.tick.reset import ResetAction, ResetChange
from manta_trading.data.tick.run_context import TickPreflightError
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
    assert names == ["estimate", "adopt", "reset"]


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
    ],
)
def test_adopt_exit_codes(writer_run: None, exc: BaseException, code: int) -> None:
    with _adopt_returning(exc):
        result = runner.invoke(app, [*ADOPT, "--json"])
    assert result.exit_code == code, result.output
    assert json.loads(result.stderr)["error"] == str(exc)


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
