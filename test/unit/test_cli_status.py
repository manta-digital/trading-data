"""Tests for mt status CLI command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_db_settings():
    """Return a mock Settings with no DB URLs configured."""
    s = MagicMock()
    s.market_db_url = None
    s.timescale_db_url = None
    s.databento_api_key = None
    return s


def _patch_no_db():
    """Context manager that patches Settings to have no DB URLs."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        with patch("manta_trading.cli.app.Settings", return_value=_no_db_settings()), \
             patch("manta_trading.cli.app.setup_logging"):
            yield

    return _cm()


class TestStatusOverview:
    """Verify mt status."""

    def test_exit_code(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0

    def test_contains_provider_section(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status"])
        assert "Provider" in result.output or "provider" in result.output.lower()

    def test_contains_database_section(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status"])
        assert "Database" in result.output or "database" in result.output.lower()

    def test_shows_all_provider_names(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status"])
        assert "databento" in result.output
        assert "flatfile" in result.output

    def test_db_not_configured(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status"])
        assert "not configured" in result.output


class TestStatusJson:
    """Verify mt status --json."""

    def test_json_valid(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_json_has_providers_key(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status", "--json"])
        data = json.loads(result.output)
        assert "providers" in data
        assert isinstance(data["providers"], list)
        assert len(data["providers"]) == 2

    def test_json_has_database_key(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status", "--json"])
        data = json.loads(result.output)
        assert "database" in data
        assert isinstance(data["database"], dict)

    def test_json_db_not_configured(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status", "--json"])
        data = json.loads(result.output)
        assert data["database"]["configured"] is False
        assert data["database"]["connected"] is False

    def test_json_provider_auth_status(self):
        with _patch_no_db():
            result = runner.invoke(app, ["status", "--json"])
        data = json.loads(result.output)
        for p in data["providers"]:
            assert "name" in p
            assert "auth_valid" in p


class TestStatusDbConnectivity:
    """Verify DB connectivity checks in mt status."""

    def test_db_configured_connected(self):
        with patch(
            "manta_trading.cli.commands.status._check_db_connectivity",
            return_value=(True, "connected"),
        ):
            result = runner.invoke(
                app, ["status", "--json"],
                env={"MT_TIMESCALE_DB_URL": "postgresql://localhost/trading"},
            )
        data = json.loads(result.output)
        assert data["database"]["configured"] is True
        assert data["database"]["connected"] is True

    def test_db_configured_unreachable(self):
        with patch(
            "manta_trading.cli.commands.status._check_db_connectivity",
            return_value=(False, "connection refused"),
        ):
            result = runner.invoke(
                app, ["status", "--json"],
                env={"MT_TIMESCALE_DB_URL": "postgresql://localhost/trading"},
            )
        data = json.loads(result.output)
        assert data["database"]["configured"] is True
        assert data["database"]["connected"] is False
        assert "timescale_error" in data["database"]

    def test_db_url_redacted_in_output(self):
        with patch(
            "manta_trading.cli.commands.status._check_db_connectivity",
            return_value=(True, "connected"),
        ):
            result = runner.invoke(
                app, ["status", "--json"],
                env={"MT_TIMESCALE_DB_URL": "postgresql://user:secret@localhost/trading"},
            )
        data = json.loads(result.output)
        assert "secret" not in data["database"]["timescale_url"]
        assert "***" in data["database"]["timescale_url"]
