"""Unit tests for ``manta_trading.data.lists``."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from manta_trading.data.lists import (
    ListNotFoundError,
    ListsConfigError,
    intersect_with_active,
    load_lists,
    refresh_sp500,
    resolve_list,
)


def write_yaml(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "symbol-lists.yaml"
    cfg.write_text(body)
    return cfg


def test_load_inline_and_file_source(tmp_path: Path):
    (tmp_path / "lists").mkdir()
    (tmp_path / "lists" / "snap.txt").write_text("# header\nFOO\nBAR\n\n# blank ok\nBAZ\n")
    cfg = write_yaml(
        tmp_path,
        """
lists:
  small:
    description: "inline"
    symbols: [AAPL, MSFT]
  big:
    description: "file"
    source: file:lists/snap.txt
""",
    )
    lists = load_lists(cfg)
    assert lists["small"] == ["AAPL", "MSFT"]
    assert lists["big"] == ["FOO", "BAR", "BAZ"]


def test_resolve_list_unknown_raises(tmp_path: Path):
    cfg = write_yaml(
        tmp_path, "lists:\n  one:\n    description: x\n    symbols: [X]\n"
    )
    with pytest.raises(ListNotFoundError):
        resolve_list("missing", cfg)


def test_load_missing_file_raises(tmp_path: Path):
    with pytest.raises(ListsConfigError):
        load_lists(tmp_path / "nope.yaml")


def test_load_malformed_yaml_raises(tmp_path: Path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("lists: [::: not yaml")
    with pytest.raises(ListsConfigError):
        load_lists(cfg)


def test_load_rejects_both_inline_and_source(tmp_path: Path):
    cfg = write_yaml(
        tmp_path,
        """
lists:
  bad:
    description: "ambiguous"
    symbols: [A]
    source: file:other.txt
""",
    )
    with pytest.raises(ListsConfigError):
        load_lists(cfg)


def test_load_rejects_missing_lists_key(tmp_path: Path):
    cfg = tmp_path / "x.yaml"
    cfg.write_text("not_lists: {}\n")
    with pytest.raises(ListsConfigError):
        load_lists(cfg)


def test_load_rejects_missing_source_file(tmp_path: Path):
    cfg = write_yaml(
        tmp_path,
        """
lists:
  big:
    description: "missing"
    source: file:nowhere.txt
""",
    )
    with pytest.raises(ListsConfigError):
        load_lists(cfg)


def test_intersect_with_active_filters_and_logs(caplog: pytest.LogCaptureFixture):
    # Mock psycopg connection: only AAPL and MSFT active.
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [("AAPL",), ("MSFT",)]
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    with caplog.at_level(logging.WARNING, logger="manta_trading.data.lists"):
        out = intersect_with_active(["AAPL", "MSFT", "GHOST"], mock_conn)

    assert out == ["AAPL", "MSFT"]
    assert any("missing or delisted" in r.message for r in caplog.records)


def test_intersect_with_active_empty_input():
    mock_conn = MagicMock()
    assert intersect_with_active([], mock_conn) == []
    mock_conn.cursor.assert_not_called()


def test_refresh_sp500_writes_snapshot(tmp_path: Path):
    snap = tmp_path / "lists" / "sp500.txt"
    payload = {
        "Components": {
            "0": {"Code": "AAPL", "Name": "Apple"},
            "1": {"Code": "MSFT", "Name": "Microsoft"},
        }
    }
    n = refresh_sp500(snap, lambda: payload)
    assert n == 2
    assert snap.read_text().splitlines() == ["AAPL", "MSFT"]


def test_refresh_sp500_accepts_list_components(tmp_path: Path):
    snap = tmp_path / "sp500.txt"
    payload = {"Components": [{"Code": "AAPL"}, {"Code": "MSFT"}]}
    assert refresh_sp500(snap, lambda: payload) == 2


def test_refresh_sp500_malformed_payload_leaves_file_untouched(tmp_path: Path):
    snap = tmp_path / "sp500.txt"
    snap.write_text("PRESERVE_ME\n")
    with pytest.raises(ListsConfigError):
        refresh_sp500(snap, lambda: {"NoComponents": []})
    assert snap.read_text() == "PRESERVE_ME\n"


def test_refresh_sp500_missing_code_field_raises(tmp_path: Path):
    snap = tmp_path / "sp500.txt"
    payload = {"Components": [{"Name": "Apple"}]}
    with pytest.raises(ListsConfigError):
        refresh_sp500(snap, lambda: payload)


def test_refresh_sp500_zero_components_raises(tmp_path: Path):
    snap = tmp_path / "sp500.txt"
    with pytest.raises(ListsConfigError):
        refresh_sp500(snap, lambda: {"Components": []})
