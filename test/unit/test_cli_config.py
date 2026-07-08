"""Tests for the mt config CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()


def _isolated_config(tmp_path: Path):
    """Return a context manager that redirects config paths to tmp_path."""
    user_dir = tmp_path / "user_config"
    user_dir.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    user_toml = user_dir / "config.toml"
    project_toml = project_dir / ".manta-trading.toml"

    return (
        patch(
            "manta_trading.config.manager.user_config_path",
            return_value=user_toml,
        ),
        patch(
            "manta_trading.config.manager.project_config_path",
            return_value=project_toml,
        ),
        str(project_dir),
    )


class TestConfigList:
    """Verify mt config list."""

    def test_list_shows_all_keys(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(app, ["config", "list", "--cwd", cwd])
        assert result.exit_code == 0
        assert "default_provider" in result.output
        assert "output_format" in result.output
        assert "data_dir" in result.output

    def test_list_shows_defaults(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(app, ["config", "list", "--cwd", cwd])
        assert result.exit_code == 0
        assert "default" in result.output

    def test_list_json_returns_valid_json_array(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app, ["config", "list", "--json", "--cwd", cwd]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) > 0
        keys_in_result = {entry["key"] for entry in data}
        assert "output_format" in keys_in_result
        # Verify required fields
        for entry in data:
            assert "key" in entry
            assert "value" in entry
            assert "source" in entry
            assert "description" in entry


class TestConfigGet:
    """Verify mt config get."""

    def test_get_default_value(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app, ["config", "get", "output_format", "--cwd", cwd]
            )
        assert result.exit_code == 0
        assert "text" in result.output
        assert "default" in result.output

    def test_get_nonexistent_key_errors(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app, ["config", "get", "nonexistent_key", "--cwd", cwd]
            )
        assert result.exit_code == 1
        assert "Unknown config key" in result.output
        assert "Available keys" in result.output

    def test_get_json_returns_valid_json_object(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app, ["config", "get", "output_format", "--json", "--cwd", cwd]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["key"] == "output_format"
        assert data["value"] == "text"
        assert data["source"] == "default"

    def test_get_nonexistent_key_json_errors(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app,
                ["config", "get", "nonexistent_key", "--json", "--cwd", cwd],
            )
        assert result.exit_code == 1


class TestConfigSet:
    """Verify mt config set."""

    def test_set_and_get_user(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app, ["config", "set", "output_format", "json", "--cwd", cwd]
            )
            assert result.exit_code == 0
            assert "Set output_format = json" in result.output

            result = runner.invoke(
                app, ["config", "get", "output_format", "--cwd", cwd]
            )
            assert result.exit_code == 0
            assert "json" in result.output
            assert "user" in result.output

    def test_set_project_overrides_user(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            runner.invoke(
                app, ["config", "set", "output_format", "json", "--cwd", cwd]
            )
            runner.invoke(
                app,
                [
                    "config", "set", "output_format", "text",
                    "--project", "--cwd", cwd,
                ],
            )
            result = runner.invoke(
                app, ["config", "get", "output_format", "--cwd", cwd]
            )
            assert result.exit_code == 0
            assert "text" in result.output
            assert "project" in result.output

    def test_set_nonexistent_key_errors(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app,
                ["config", "set", "nonexistent_key", "foo", "--cwd", cwd],
            )
            assert result.exit_code == 1
            assert "Available keys" in result.output

    def test_set_invalid_choice_errors(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app,
                ["config", "set", "output_format", "banana", "--cwd", cwd],
            )
            assert result.exit_code == 1
            assert "Invalid value" in result.output
            assert "banana" in result.output


class TestConfigPath:
    """Verify mt config path."""

    def test_path_shows_both_locations(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(app, ["config", "path", "--cwd", cwd])
        assert result.exit_code == 0
        assert "User:" in result.output
        assert "Project:" in result.output

    def test_path_json_returns_valid_json(self, tmp_path):
        p1, p2, cwd = _isolated_config(tmp_path)
        with p1, p2:
            result = runner.invoke(
                app, ["config", "path", "--json", "--cwd", cwd]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "user" in data
        assert "project" in data
        assert "path" in data["user"]
        assert "exists" in data["user"]
        assert "path" in data["project"]
        assert "exists" in data["project"]
