"""Tests for the ConfigManager (TOML persistent config)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from manta_trading.config.manager import (
    _read_toml,
    get_config,
    load_config,
    project_config_path,
    set_config,
    user_config_path,
)


@pytest.fixture()
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect user and project config paths to tmp_path."""
    user_dir = tmp_path / "user_config"
    user_dir.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    user_toml = user_dir / "config.toml"
    project_toml = project_dir / ".manta-trading.toml"

    with (
        patch(
            "manta_trading.config.manager.user_config_path",
            return_value=user_toml,
        ),
        patch(
            "manta_trading.config.manager.project_config_path",
            return_value=project_toml,
        ),
    ):
        yield user_toml, project_toml, str(project_dir)


class TestLoadConfigDefaults:
    """Verify load_config returns defaults when no TOML files exist."""

    def test_returns_all_defaults(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        cfg = load_config(cwd)
        assert cfg["default_provider"] is None
        assert cfg["output_format"] == "text"
        assert cfg["data_dir"] is None

    def test_all_config_keys_present(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        from manta_trading.config.keys import CONFIG_KEYS

        cfg = load_config(cwd)
        assert set(cfg.keys()) == set(CONFIG_KEYS.keys())


class TestSetGetRoundtrip:
    """Verify set_config + get_config roundtrip."""

    def test_user_config_roundtrip(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        set_config("output_format", "json", cwd=cwd)
        value, source = get_config("output_format", cwd=cwd)
        assert value == "json"
        assert source == "user"

    def test_project_config_roundtrip(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        set_config("output_format", "json", project=True, cwd=cwd)
        value, source = get_config("output_format", cwd=cwd)
        assert value == "json"
        assert source == "project"


class TestPrecedence:
    """Verify precedence: project TOML overrides user TOML overrides defaults."""

    def test_user_overrides_default(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        set_config("output_format", "json", cwd=cwd)
        value, source = get_config("output_format", cwd=cwd)
        assert value == "json"
        assert source == "user"

    def test_project_overrides_user(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        set_config("output_format", "json", cwd=cwd)
        set_config("output_format", "text", project=True, cwd=cwd)
        value, source = get_config("output_format", cwd=cwd)
        assert value == "text"
        assert source == "project"


class TestUnknownKey:
    """Verify unknown key handling."""

    def test_get_unknown_key_raises(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        with pytest.raises(KeyError, match="Unknown config key"):
            get_config("nonexistent_key", cwd=cwd)

    def test_set_unknown_key_raises(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        with pytest.raises(KeyError, match="Unknown config key"):
            set_config("nonexistent_key", "value", cwd=cwd)

    def test_error_lists_available_keys(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        with pytest.raises(KeyError, match="Available keys"):
            get_config("nonexistent_key", cwd=cwd)


class TestChoicesValidation:
    """Verify choices are enforced on set_config."""

    def test_valid_choice_accepted(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        set_config("output_format", "json", cwd=cwd)
        value, _ = get_config("output_format", cwd=cwd)
        assert value == "json"

    def test_invalid_choice_rejected(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        with pytest.raises(ValueError, match="Invalid value.*banana"):
            set_config("output_format", "banana", cwd=cwd)

    def test_key_without_choices_accepts_any(self, isolated_config) -> None:
        _user_toml, _project_toml, cwd = isolated_config
        set_config("data_dir", "/any/path/is/fine", cwd=cwd)
        value, _ = get_config("data_dir", cwd=cwd)
        assert value == "/any/path/is/fine"


class TestReadToml:
    """Verify _read_toml returns empty dict for nonexistent file."""

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        result = _read_toml(tmp_path / "does_not_exist.toml")
        assert result == {}

    def test_existing_file_returns_data(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "test.toml"
        toml_file.write_text('key = "value"\n')
        result = _read_toml(toml_file)
        assert result == {"key": "value"}


class TestConfigPaths:
    """Verify config path resolution."""

    def test_user_config_path_is_absolute(self) -> None:
        assert user_config_path().is_absolute()

    def test_project_config_path_is_absolute(self) -> None:
        assert project_config_path(".").is_absolute()

    def test_project_config_uses_cwd(self, tmp_path: Path) -> None:
        path = project_config_path(str(tmp_path))
        assert path.parent == tmp_path
        assert path.name == ".manta-trading.toml"
