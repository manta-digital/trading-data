"""Unit tests for `mt update` (slice 909).

Network and subprocess boundaries are mocked with ``monkeypatch`` — no test
ever reaches PyPI, spawns a process, or touches a database.
"""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import update as update_mod
from manta_trading.cli.commands.update import (
    InstallMethod,
    detect_install_method,
    fetch_latest_version,
    report_pending_migrations,
    upgrade_command,
)
from manta_trading.constants import DISTRIBUTION_NAME

# -- Fakes --------------------------------------------------------------------


class _FakeDistribution:
    """Stands in for ``importlib.metadata.Distribution``."""

    def __init__(self, direct_url: str | None) -> None:
        self._direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        assert filename == "direct_url.json"
        return self._direct_url


class _FakeResponse:
    def __init__(self, payload: Any, *, status_error: bool = False) -> None:
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error:
            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=httpx.Request("GET", "https://pypi.org/"),
                response=httpx.Response(404),
            )

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _install_distribution(
    monkeypatch: pytest.MonkeyPatch, direct_url: str | None
) -> None:
    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        lambda name: _FakeDistribution(direct_url),
    )


@pytest.fixture(autouse=True)
def _no_uv_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point ``sys.prefix`` at a receipt-free directory by default.

    Keeps detection tests independent of how the *test runner* itself was
    installed; the uv-tool test opts back in by writing the receipt.
    """
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(update_mod.sys, "prefix", str(prefix))


def _missing_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(name: str) -> Any:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", _raise)


# -- detect_install_method (2.1 / 2.2) ----------------------------------------


def test_detect_missing_metadata_is_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _missing_distribution(monkeypatch)
    assert detect_install_method() is InstallMethod.EDITABLE_OR_SOURCE


def test_detect_editable_direct_url_is_editable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_distribution(
        monkeypatch,
        json.dumps({"url": "file:///repo", "dir_info": {"editable": True}}),
    )
    monkeypatch.setattr(update_mod.sys, "executable", "/usr/bin/python3")
    assert detect_install_method() is InstallMethod.EDITABLE_OR_SOURCE


def test_detect_uv_tool_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_distribution(monkeypatch, None)
    monkeypatch.setattr(
        update_mod.sys,
        "executable",
        f"/home/dev/.local/share/uv/tools/{DISTRIBUTION_NAME}/bin/python",
    )
    assert detect_install_method() is InstallMethod.UV_TOOL


def test_detect_uv_receipt_beats_relocated_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relocated UV_TOOL_DIR has no uv/tools segments — the receipt wins."""
    _install_distribution(monkeypatch, None)
    prefix = tmp_path / "relocated" / DISTRIBUTION_NAME
    prefix.mkdir(parents=True)
    (prefix / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    monkeypatch.setattr(update_mod.sys, "prefix", str(prefix))
    monkeypatch.setattr(update_mod.sys, "executable", str(prefix / "bin/python"))
    assert detect_install_method() is InstallMethod.UV_TOOL


def test_detect_pipx_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_distribution(monkeypatch, None)
    monkeypatch.setattr(
        update_mod.sys,
        "executable",
        f"/home/dev/.local/pipx/venvs/{DISTRIBUTION_NAME}/bin/python",
    )
    assert detect_install_method() is InstallMethod.PIPX


def test_detect_plain_venv_is_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_distribution(monkeypatch, json.dumps({"url": "https://pypi.org/x.whl"}))
    monkeypatch.setattr(update_mod.sys, "executable", "/opt/venvs/mt/bin/python")
    assert detect_install_method() is InstallMethod.PIP


def test_detect_non_editable_direct_url_is_pip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_distribution(
        monkeypatch,
        json.dumps({"url": "file:///repo", "dir_info": {"editable": False}}),
    )
    monkeypatch.setattr(update_mod.sys, "executable", "/opt/venvs/mt/bin/python")
    assert detect_install_method() is InstallMethod.PIP


# -- fetch_latest_version (2.3 / 2.4) -----------------------------------------


def _patch_get(monkeypatch: pytest.MonkeyPatch, result: Any) -> dict[str, Any]:
    """Patch ``httpx.get``; return a dict recording the call arguments."""
    recorded: dict[str, Any] = {}

    def _get(url: str, **kwargs: Any) -> Any:
        recorded["url"] = url
        recorded["kwargs"] = kwargs
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(update_mod.httpx, "get", _get)
    return recorded


def test_fetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _patch_get(monkeypatch, _FakeResponse({"info": {"version": "0.7.0"}}))
    assert fetch_latest_version() == "0.7.0"
    assert recorded["url"] == f"https://pypi.org/pypi/{DISTRIBUTION_NAME}/json"
    assert recorded["kwargs"]["timeout"] == update_mod.REGISTRY_TIMEOUT


def test_fetch_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, httpx.TimeoutException("timed out"))
    assert fetch_latest_version() is None


def test_fetch_non_200_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(None, status_error=True))
    assert fetch_latest_version() is None


def test_fetch_invalid_json_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(ValueError("not json")))
    assert fetch_latest_version() is None


def test_fetch_missing_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse({"info": {}}))
    assert fetch_latest_version() is None


def test_fetch_mistyped_version_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get(monkeypatch, _FakeResponse({"info": {"version": 123}}))
    assert fetch_latest_version() is None


# -- upgrade_command (2.5 / 2.6) ----------------------------------------------


def test_upgrade_command_uv_tool_argv() -> None:
    assert upgrade_command(InstallMethod.UV_TOOL) == [
        "uv",
        "tool",
        "install",
        "--upgrade",
        DISTRIBUTION_NAME,
    ]


@pytest.mark.parametrize(
    "method",
    [InstallMethod.PIPX, InstallMethod.PIP, InstallMethod.EDITABLE_OR_SOURCE],
)
def test_upgrade_command_other_methods_are_not_auto_run(
    method: InstallMethod,
) -> None:
    assert upgrade_command(method) is None


def test_every_install_method_has_manual_command() -> None:
    assert set(update_mod.MANUAL_UPGRADE_COMMAND) == set(InstallMethod)


# -- report_pending_migrations (2.7 / 2.8) ------------------------------------


def _patch_run(monkeypatch: pytest.MonkeyPatch, result: Any) -> list[list[str]]:
    calls: list[list[str]] = []

    def _run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(argv)
        assert kwargs["timeout"] == update_mod.UPDATE_MIGRATE_PROBE_TIMEOUT
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(update_mod.subprocess, "run", _run)
    return calls


def test_probe_counts_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "connected": True,
            "applied": [{"id": "001"}],
            "pending": [{"id": "002"}, {"id": "003"}],
        }
    )
    calls = _patch_run(monkeypatch, _FakeCompleted(0, payload))
    assert report_pending_migrations() == 2
    assert calls[0][1:] == ["data", "migrate", "status", "--json"]


def test_probe_non_zero_exit_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _FakeCompleted(1, ""))
    assert report_pending_migrations() is None


def test_probe_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(
        monkeypatch,
        subprocess.TimeoutExpired(cmd="mt", timeout=30.0),
    )
    assert report_pending_migrations() is None


def test_probe_garbage_output_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _FakeCompleted(0, "not json at all"))
    assert report_pending_migrations() is None


def test_probe_disconnected_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {"connected": False, "error": "no url", "applied": [], "pending": []}
    )
    _patch_run(monkeypatch, _FakeCompleted(0, payload))
    assert report_pending_migrations() is None


def test_probe_missing_binary_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, FileNotFoundError("mt"))
    assert report_pending_migrations() is None


def test_update_module_never_imports_db_layer() -> None:
    """D6: the update path must be structurally incapable of a DB call."""
    source = update_mod.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    for forbidden in ("psycopg", "TimescaleMinuteDataDB", "manta_trading.data"):
        assert forbidden not in text


# -- Behavior matrix through the CLI (3.3) ------------------------------------

runner = CliRunner()


class _Harness:
    """Records every side effect the `mt update` command can perform."""

    def __init__(self) -> None:
        self.method = InstallMethod.UV_TOOL
        self.current = "0.6.1"
        self.latest: str | None = "0.7.0"
        self.interactive = False
        self.confirm_answer = True
        self.uv_on_path: str | None = "/usr/bin/uv"
        self.upgrade_result: Any = _FakeCompleted(0, "")
        self.probe_result: Any = _FakeCompleted(
            0, json.dumps({"connected": True, "applied": [], "pending": []})
        )
        self.runs: list[list[str]] = []
        self.fetch_calls = 0
        self.prompts = 0

    @property
    def upgrade_runs(self) -> list[list[str]]:
        return [argv for argv in self.runs if "migrate" not in argv]

    @property
    def probe_runs(self) -> list[list[str]]:
        return [argv for argv in self.runs if "migrate" in argv]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Harness]:
    state = _Harness()

    def _fetch() -> str | None:
        state.fetch_calls += 1
        return state.latest

    def _confirm(prompt: str) -> bool:
        state.prompts += 1
        return state.confirm_answer

    def _run(argv: list[str], **kwargs: Any) -> Any:
        state.runs.append(list(argv))
        result = state.probe_result if "migrate" in argv else state.upgrade_result
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(update_mod, "detect_install_method", lambda: state.method)
    monkeypatch.setattr(update_mod, "_current_version", lambda: state.current)
    monkeypatch.setattr(update_mod, "fetch_latest_version", _fetch)
    monkeypatch.setattr(update_mod, "_is_interactive", lambda: state.interactive)
    monkeypatch.setattr(update_mod.typer, "confirm", _confirm)
    monkeypatch.setattr(update_mod.shutil, "which", lambda name: state.uv_on_path)
    monkeypatch.setattr(update_mod.subprocess, "run", _run)

    with (
        patch("manta_trading.cli.app.Settings", return_value=MagicMock()),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield state


def _invoke(*args: str) -> Any:
    return runner.invoke(app, ["update", *args])


def _flat(result: Any) -> str:
    """Output with Rich's line wrapping collapsed to single spaces."""
    return " ".join(result.output.split())


def test_cli_up_to_date(harness: _Harness) -> None:
    harness.latest = harness.current
    result = _invoke()
    assert result.exit_code == 0, result.output
    assert "up to date" in _flat(result)
    assert harness.prompts == 0
    assert harness.runs == []


def test_cli_confirm_accepted_upgrades_and_probes(harness: _Harness) -> None:
    harness.interactive = True
    harness.probe_result = _FakeCompleted(
        0, json.dumps({"connected": True, "applied": [], "pending": [{"id": "002"}]})
    )
    result = _invoke()
    assert result.exit_code == 0, result.output
    assert harness.prompts == 1
    assert harness.upgrade_runs == [
        ["uv", "tool", "install", "--upgrade", DISTRIBUTION_NAME]
    ]
    assert len(harness.probe_runs) == 1
    flat = _flat(result)
    assert f"Updated {DISTRIBUTION_NAME} to 0.7.0" in flat
    assert "1 migration(s) pending" in flat


def test_cli_confirm_declined_does_nothing(harness: _Harness) -> None:
    harness.interactive = True
    harness.confirm_answer = False
    result = _invoke()
    assert result.exit_code == 0, result.output
    assert harness.prompts == 1
    assert harness.runs == []


def test_cli_yes_skips_prompt(harness: _Harness) -> None:
    harness.interactive = True
    result = _invoke("--yes")
    assert result.exit_code == 0, result.output
    assert harness.prompts == 0
    assert len(harness.upgrade_runs) == 1


def test_cli_non_tty_without_yes_reports_only(harness: _Harness) -> None:
    result = _invoke()
    assert result.exit_code == 0, result.output
    flat = _flat(result)
    assert "Update available: 0.6.1 → 0.7.0" in flat
    assert "--yes" in flat
    assert harness.prompts == 0
    assert harness.runs == []


def test_cli_json_is_a_pure_query(harness: _Harness) -> None:
    harness.interactive = True
    result = _invoke("--json", "--yes")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "current": "0.6.1",
        "latest": "0.7.0",
        "update_available": True,
        "install_method": "uv-tool",
    }
    assert harness.runs == []
    assert harness.prompts == 0


def test_cli_json_editable_makes_no_network_call(harness: _Harness) -> None:
    harness.method = InstallMethod.EDITABLE_OR_SOURCE
    result = _invoke("--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "current": "0.6.1",
        "latest": None,
        "update_available": False,
        "install_method": "editable-or-source",
    }
    assert harness.fetch_calls == 0


def test_cli_json_registry_failure(harness: _Harness) -> None:
    harness.latest = None
    result = _invoke("--json")
    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "current": "0.6.1",
        "latest": None,
        "update_available": False,
        "error": "registry unreachable",
    }


def test_cli_editable_human_mode(harness: _Harness) -> None:
    harness.method = InstallMethod.EDITABLE_OR_SOURCE
    result = _invoke()
    assert result.exit_code == 0, result.output
    flat = _flat(result)
    assert "Developer install detected" in flat
    assert "git pull && uv sync" in flat
    assert harness.fetch_calls == 0
    assert harness.runs == []


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (InstallMethod.PIPX, f"pipx upgrade {DISTRIBUTION_NAME}"),
        (InstallMethod.PIP, f"pip install --upgrade {DISTRIBUTION_NAME}"),
    ],
)
def test_cli_pipx_and_pip_print_without_running(
    harness: _Harness, method: InstallMethod, expected: str
) -> None:
    harness.method = method
    result = _invoke("--yes")
    assert result.exit_code == 0, result.output
    assert expected in _flat(result)
    assert harness.runs == []


def test_cli_missing_uv_degrades_to_printing(harness: _Harness) -> None:
    harness.uv_on_path = None
    result = _invoke("--yes")
    assert result.exit_code == 0, result.output
    assert f"uv tool install --upgrade {DISTRIBUTION_NAME}" in _flat(result)
    assert harness.runs == []


def test_cli_registry_unreachable_human_mode(harness: _Harness) -> None:
    harness.latest = None
    result = _invoke()
    assert result.exit_code == 1
    flat = _flat(result)
    assert "Could not reach PyPI" in flat
    assert "Traceback" not in flat


def test_cli_upgrade_non_zero_exit(harness: _Harness) -> None:
    harness.upgrade_result = _FakeCompleted(2, "")
    result = _invoke("--yes")
    assert result.exit_code == 1
    assert "Upgrade failed (exit 2)" in _flat(result)
    assert harness.probe_runs == []


def test_cli_upgrade_timeout(harness: _Harness) -> None:
    harness.upgrade_result = subprocess.TimeoutExpired(cmd="uv", timeout=600.0)
    result = _invoke("--yes")
    assert result.exit_code == 1
    flat = _flat(result)
    assert "timed out" in flat
    assert f"uv tool install --upgrade {DISTRIBUTION_NAME}" in flat
    assert harness.probe_runs == []


def test_cli_probe_degradation_still_exits_zero(harness: _Harness) -> None:
    harness.probe_result = _FakeCompleted(1, "")
    result = _invoke("--yes")
    assert result.exit_code == 0, result.output
    assert update_mod.MIGRATION_POINTER in _flat(result)
