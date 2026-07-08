"""Integration tests for ``mt data lists`` CLI (slice 146 T8).

These exercise the CLI surface against the committed
``config/symbol-lists.yaml``. The ``ls`` and ``show`` commands do not
require a database; ``refresh-sp500`` is exercised against an injected
HTTP fake (no real EODHD call) by writing a temporary config and
patching the fetch callable.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app


def _runner() -> CliRunner:
    # mix_stderr is the default; explicit for clarity in CI logs.
    return CliRunner()


def test_lists_ls_includes_priority1():
    runner = _runner()
    result = runner.invoke(app, ["data", "lists", "ls"])
    assert result.exit_code == 0, result.output
    assert "priority1" in result.output


def test_lists_show_priority1_emits_ten_symbols():
    runner = _runner()
    result = runner.invoke(app, ["data", "lists", "show", "priority1"])
    assert result.exit_code == 0, result.output
    expected = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "GOOGL", "META",
                "TSLA", "AMZN", "BRK-B"]
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines == expected


def test_lists_show_unknown_exits_nonzero():
    runner = _runner()
    result = runner.invoke(app, ["data", "lists", "show", "definitely-not-a-list"])
    assert result.exit_code != 0


def test_lists_refresh_sp500_writes_snapshot(tmp_path: Path, monkeypatch):
    # The refresh-sp500 command requires MT_EODHD_API_KEY to be set
    # because _validate_credentials gates on it. Inject any non-empty
    # value; we patch the actual httpx call so the key never travels.
    monkeypatch.setenv("MT_EODHD_API_KEY", "test-key")

    snapshot = tmp_path / "sp500-snapshot.txt"
    fake_payload = {
        "Components": {
            "0": {"Code": "AAPL", "Name": "Apple"},
            "1": {"Code": "MSFT", "Name": "Microsoft"},
            "2": {"Code": "GOOGL", "Name": "Alphabet"},
        }
    }

    runner = _runner()
    # Patch httpx.Client.get so the command's inner _fetch returns
    # ``fake_payload`` instead of touching the network.
    class _FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return fake_payload

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> bool:
            return False

        def get(self, *_args, **_kwargs):
            return _FakeResp()

    with patch("httpx.Client", _FakeClient):
        result = runner.invoke(
            app,
            ["data", "lists", "refresh-sp500", "--snapshot", str(snapshot)],
        )

    assert result.exit_code == 0, result.output
    assert snapshot.exists()
    assert snapshot.read_text().splitlines() == ["AAPL", "MSFT", "GOOGL"]
