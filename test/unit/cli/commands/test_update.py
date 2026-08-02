"""Unit tests for `mt update` (slice 909).

Network and subprocess boundaries are mocked with ``monkeypatch`` — no test
ever reaches PyPI, spawns a process, or touches a database.
"""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
from typing import Any

import httpx
import pytest

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
    text = open(source, encoding="utf-8").read()
    for forbidden in ("psycopg", "TimescaleMinuteDataDB", "manta_trading.data"):
        assert forbidden not in text
