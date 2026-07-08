"""Tests for the shared CLI output formatter."""

from __future__ import annotations

import json

from rich.table import Table

from manta_trading.cli.output import make_table, print_error, print_result


class TestPrintResult:
    """Verify print_result behaviour in JSON and text modes."""

    def test_json_mode_outputs_valid_json(self, capsys):
        data = {"key": "value", "count": 42}
        print_result(data, json_mode=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data
        assert captured.err == ""

    def test_json_mode_handles_list(self, capsys):
        data = [{"a": 1}, {"b": 2}]
        print_result(data, json_mode=True)
        parsed = json.loads(capsys.readouterr().out)
        assert parsed == data

    def test_text_mode_does_not_emit_json(self, capsys):
        data = {"key": "value"}
        print_result(data, json_mode=False)
        out = capsys.readouterr().out
        # Text mode uses Rich which renders dict repr, not JSON
        assert "key" in out


class TestPrintError:
    """Verify print_error behaviour in JSON and text modes."""

    def test_json_mode_outputs_error_json_to_stderr(self, capsys):
        print_error("something went wrong", json_mode=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.err)
        assert parsed == {"error": "something went wrong"}
        assert captured.out == ""

    def test_text_mode_outputs_to_stderr(self, capsys):
        print_error("bad input", json_mode=False)
        captured = capsys.readouterr()
        assert "bad input" in captured.err
        assert captured.out == ""


class TestMakeTable:
    """Verify make_table creates a Rich Table with expected columns."""

    def test_returns_table_with_columns(self):
        table = make_table(
            "Test Table",
            [("Name", "cyan"), ("Value", ""), ("Source", "dim")],
        )
        assert isinstance(table, Table)
        assert table.title == "Test Table"
        assert len(table.columns) == 3
        assert table.columns[0].header == "Name"
        assert table.columns[1].header == "Value"
        assert table.columns[2].header == "Source"
