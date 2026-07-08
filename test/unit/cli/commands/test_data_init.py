"""Unit tests for `mt data init` command (slice 156)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()


def _settings(*, timescale_url: str | None = "postgresql://ts/db"):
    s = MagicMock()
    s.timescale_db_url = timescale_url
    s.eodhd_api_key = None
    return s


def _patch_app(settings):
    import contextlib

    @contextlib.contextmanager
    def _cm():
        with (
            patch("manta_trading.cli.app.Settings", return_value=settings),
            patch("manta_trading.cli.app.setup_logging"),
        ):
            yield

    return _cm()


def _mock_db(applied_now=None, applied_total=None, pending=None):
    """Build a mock TimescaleMinuteDataDB.

    apply_schema_migrations() returns ``applied_now``;
    list_migration_state() returns ids derived from ``applied_total`` and
    ``pending`` lists.
    """
    applied_now = applied_now or []
    applied_total = applied_total or list(applied_now)
    pending = pending or []
    db = MagicMock()
    db.apply_schema_migrations.return_value = applied_now
    db.list_migration_state.return_value = {
        "applied": [
            {"id": mid, "description": "", "applied_at": "2026-05-09"}
            for mid in applied_total
        ],
        "pending": [{"id": mid, "description": ""} for mid in pending],
    }
    return db


class TestDataInit:
    def test_missing_url_exits_with_error(self):
        s = _settings(timescale_url=None)
        with _patch_app(s):
            result = runner.invoke(app, ["data", "init"])
        assert result.exit_code != 0
        assert "MT_TIMESCALE_DB_URL" in result.output

    def test_default_invocation_calls_apply_once(self):
        s = _settings()
        db = _mock_db(applied_now=["038_create_acquisition_state"])
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._create_timescale_db",
                return_value=db,
            ):
                result = runner.invoke(app, ["data", "init"])
        assert result.exit_code == 0, result.output
        assert db.apply_schema_migrations.call_count == 1
        # Status snapshot is also fetched after apply
        assert db.list_migration_state.call_count == 1
        assert db.close.called

    def test_validate_only_does_not_apply(self):
        s = _settings()
        db = _mock_db(applied_total=["001_schema_migrations"], pending=["002_x"])
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._create_timescale_db",
                return_value=db,
            ):
                result = runner.invoke(app, ["data", "init", "--validate-only"])
        assert result.exit_code == 0, result.output
        assert db.apply_schema_migrations.call_count == 0
        assert db.list_migration_state.call_count == 1
        assert db.close.called

    def test_json_output_emits_counts(self):
        import json

        s = _settings()
        db = _mock_db(
            applied_now=["038_create_acquisition_state"],
            applied_total=["001_schema_migrations", "038_create_acquisition_state"],
            pending=[],
        )
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._create_timescale_db",
                return_value=db,
            ):
                result = runner.invoke(app, ["data", "init", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["applied_now"] == ["038_create_acquisition_state"]
        assert payload["applied_total"] == 2
        assert payload["pending_remaining"] == 0

    def test_yes_flag_is_accepted(self):
        s = _settings()
        db = _mock_db(applied_now=[])
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._create_timescale_db",
                return_value=db,
            ):
                result = runner.invoke(app, ["data", "init", "--yes"])
        assert result.exit_code == 0, result.output
