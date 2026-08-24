"""Tests for mt provider CLI commands."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()


class TestProviderList:
    """Verify mt provider list."""

    def test_list_exit_code(self):
        result = runner.invoke(app, ["provider", "list"])
        assert result.exit_code == 0

    def test_list_contains_all_providers(self):
        result = runner.invoke(app, ["provider", "list"])
        assert "databento" in result.output
        assert "flatfile" in result.output

    def test_alphavantage_removed(self):
        result = runner.invoke(app, ["provider", "list"])
        assert "alphavantage" not in result.output

    def test_list_json_valid(self):
        result = runner.invoke(app, ["provider", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 3

    def test_list_json_includes_kalshi(self):
        result = runner.invoke(app, ["provider", "list", "--json"])
        data = json.loads(result.output)
        kalshi = next(entry for entry in data if entry["name"] == "kalshi")
        assert kalshi["auth_valid"] is True
        assert kalshi["base_url"].startswith("https://external-api.kalshi.com/")

    def test_list_json_has_required_keys(self):
        result = runner.invoke(app, ["provider", "list", "--json"])
        data = json.loads(result.output)
        for entry in data:
            assert "name" in entry
            assert "provider_type" in entry
            assert "auth_valid" in entry
            assert "base_url" in entry


class TestProviderStatus:
    """Verify mt provider status."""

    def test_status_all_providers(self):
        result = runner.invoke(app, ["provider", "status"])
        assert result.exit_code == 0
        assert "databento" in result.output
        assert "flatfile" in result.output

    def test_status_single_provider(self):
        result = runner.invoke(app, ["provider", "status", "databento"])
        assert result.exit_code == 0
        assert "databento" in result.output

    def test_status_alias_resolution(self):
        result = runner.invoke(app, ["provider", "status", "bento"])
        assert result.exit_code == 0
        assert "databento" in result.output

    def test_status_alphavantage_removed(self):
        result = runner.invoke(app, ["provider", "status", "alphavantage"])
        assert result.exit_code == 1

    def test_status_nonexistent_exits_1(self):
        result = runner.invoke(app, ["provider", "status", "nonexistent"])
        assert result.exit_code == 1

    def test_status_nonexistent_shows_available(self):
        result = runner.invoke(app, ["provider", "status", "nonexistent"])
        assert "Available" in result.output or "available" in result.output.lower()

    def test_status_json_single(self):
        result = runner.invoke(
            app, ["provider", "status", "databento", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert data["name"] == "databento"

    def test_status_json_all(self):
        result = runner.invoke(app, ["provider", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 3

    def test_status_json_kalshi_auth_none(self):
        result = runner.invoke(app, ["provider", "status", "--json"])
        data = json.loads(result.output)
        kalshi = next(entry for entry in data if entry["name"] == "kalshi")
        assert kalshi["auth_type"] == "none"


class TestProviderTest:
    """Verify mt provider test."""

    def test_test_without_api_key(self):
        result = runner.invoke(
            app,
            ["provider", "test", "databento"],
            env={"MT_DATABENTO_API_KEY": ""},
        )
        assert result.exit_code == 0
        assert "not authenticated" in result.output or "✗" in result.output

    def test_test_with_api_key(self):
        result = runner.invoke(
            app,
            ["provider", "test", "databento"],
            env={"MT_DATABENTO_API_KEY": "demo-key"},
        )
        assert result.exit_code == 0
        assert "authenticated" in result.output or "✓" in result.output

    def test_test_nonexistent_exits_1(self):
        result = runner.invoke(app, ["provider", "test", "nonexistent"])
        assert result.exit_code == 1

    def test_test_json_output(self):
        result = runner.invoke(
            app, ["provider", "test", "databento", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "auth_valid" in data
        assert data["provider"] == "databento"

    def test_test_flatfile_always_valid(self):
        result = runner.invoke(app, ["provider", "test", "flatfile"])
        assert result.exit_code == 0
        assert "authenticated" in result.output or "✓" in result.output

    def test_test_alias_resolution(self):
        result = runner.invoke(app, ["provider", "test", "bento"])
        assert result.exit_code == 0
        assert "databento" in result.output
