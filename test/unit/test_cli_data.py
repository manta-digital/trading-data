"""Unit tests for mt data CLI commands."""

from __future__ import annotations

import json
from datetime import date as dt_date
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(*, timescale_url: str | None = "postgresql://ts/db",
              market_url: str | None = "postgresql://mkt/db"):
    """Return a mock Settings with configurable URLs."""
    s = MagicMock()
    s.timescale_db_url = timescale_url
    s.market_db_url = market_url
    return s


def _patch_app(settings):
    """Context manager that patches Settings and setup_logging for CLI tests."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        with patch("manta_trading.cli.app.Settings", return_value=settings), \
             patch("manta_trading.cli.app.setup_logging"):
            yield

    return _cm()


class TestDataHelp:
    """Verify help text and command discovery."""

    def test_data_help_shows_new_commands(self):
        result = runner.invoke(app, ["data", "--help"])
        assert result.exit_code == 0
        for cmd in ("get", "pull", "caggs"):
            assert cmd in result.output

    def test_data_help_does_not_show_deleted_commands(self):
        result = runner.invoke(app, ["data", "--help"])
        assert result.exit_code == 0
        assert "daily" not in result.output
        assert "minute" not in result.output
        assert "refetch" not in result.output


class TestMtHelpShowsData:
    """Verify top-level CLI shows data subcommand."""

    def test_mt_help_shows_data(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "data" in result.output




# ---------------------------------------------------------------------------
# mt data migrate apply  (Tasks 5.6a-e)
# ---------------------------------------------------------------------------


def _mock_timescale_db(applied: list[str] | None = None, state: dict | None = None) -> MagicMock:
    db = MagicMock()
    db.apply_schema_migrations.return_value = applied or []
    db.list_migration_state.return_value = state or {"applied": [], "pending": []}
    return db


class TestMigrateApply:
    def test_migrate_apply_runs_timescale_track(self):
        ts_db = _mock_timescale_db(applied=["001_schema_migrations"])
        with _patch_app(_settings()):
            with patch("manta_trading.cli.commands.data._create_timescale_db", return_value=ts_db):
                result = runner.invoke(app, ["data", "migrate", "apply"])
        assert result.exit_code == 0
        ts_db.apply_schema_migrations.assert_called_once()

    def test_migrate_apply_missing_url_exits_nonzero(self):
        with _patch_app(_settings(timescale_url=None)):
            result = runner.invoke(app, ["data", "migrate", "apply"])
        assert result.exit_code == 1

    def test_migrate_apply_json_output(self):
        ts_db = _mock_timescale_db(applied=["001_schema_migrations"])
        with _patch_app(_settings()):
            with patch("manta_trading.cli.commands.data._create_timescale_db", return_value=ts_db):
                result = runner.invoke(app, ["data", "migrate", "apply", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "applied" in data

    def test_help_shows_command(self):
        result = runner.invoke(app, ["data", "--help"])
        assert result.exit_code == 0
        assert "migrate" in result.output


# ---------------------------------------------------------------------------
# mt data migrate status
# ---------------------------------------------------------------------------


class TestMigrateStatus:
    def test_status_json_shape(self):
        ts_state = {"applied": [{"id": "001", "description": "x", "applied_at": "2026-01-01T00:00:00+00:00"}], "pending": []}
        ts_db = _mock_timescale_db(state=ts_state)
        with _patch_app(_settings()):
            with patch("manta_trading.cli.commands.data._create_timescale_db", return_value=ts_db):
                result = runner.invoke(app, ["data", "migrate", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "applied" in data
        assert "pending" in data

    def test_status_missing_url_exits_nonzero(self):
        with _patch_app(_settings(timescale_url=None)):
            result = runner.invoke(app, ["data", "migrate", "status"])
        assert result.exit_code == 1

    def test_status_pending_shown(self):
        ts_state = {"applied": [], "pending": [{"id": "001_schema_migrations", "description": "Create table"}]}
        ts_db = _mock_timescale_db(state=ts_state)
        with _patch_app(_settings()):
            with patch("manta_trading.cli.commands.data._create_timescale_db", return_value=ts_db):
                result = runner.invoke(app, ["data", "migrate", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["pending"]) == 1
        assert data["pending"][0]["id"] == "001_schema_migrations"



# ---------------------------------------------------------------------------
# TestInstrumentsList
# ---------------------------------------------------------------------------

def _make_instrument(instrument_id=1, symbol="AAPL", canonical_id="AAPL.NASDAQ",
                     venue="NASDAQ", asset_class="equity", active=True):
    """Slice 141 dropped ``Instrument.active``; the parameter is ignored."""
    del active
    from manta_trading.data.base.instrument_registry import Instrument
    return Instrument(
        instrument_id=instrument_id,
        canonical_id=canonical_id,
        symbol=symbol,
        asset_class=asset_class,
        venue=venue,
    )


class TestInstrumentsList:
    """Tests for mt data instruments list."""

    def _run_list(self, settings, *extra_args):
        with _patch_app(settings), \
             patch(
                 "manta_trading.data.base.instrument_registry.ConnectionPool",
             ) as pool_cls:
            pool_mock = MagicMock()
            pool_cls.return_value = pool_mock
            cursor_mock = MagicMock()
            cursor_mock.fetchall.return_value = []
            cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
            cursor_mock.__exit__ = MagicMock(return_value=False)
            conn_mock = MagicMock()
            conn_mock.cursor.return_value = cursor_mock
            conn_mock.__enter__ = MagicMock(return_value=conn_mock)
            conn_mock.__exit__ = MagicMock(return_value=False)
            pool_mock.connection.return_value = conn_mock
            return runner.invoke(app, ["data", "instruments", "list", *extra_args])

    def test_default_output_shows_instruments(self):
        from manta_trading.data.base.instrument_registry import Instrument
        instruments = [
            _make_instrument(1, "AAPL", "AAPL.NASDAQ", "NASDAQ", "equity"),
            _make_instrument(2, "IBM", "IBM.NYSE", "NYSE", "equity"),
        ]
        with _patch_app(_settings()), \
             patch("manta_trading.cli.commands.data._create_instrument_registry") as mock_factory:
            registry_mock = MagicMock()
            registry_mock.list_instruments.return_value = instruments
            mock_factory.return_value = registry_mock

            result = runner.invoke(app, ["data", "instruments", "list"])

        assert result.exit_code == 0
        assert "AAPL" in result.output
        assert "IBM" in result.output

    def test_json_output_is_valid_json_array(self):
        instruments = [_make_instrument()]
        with _patch_app(_settings()), \
             patch("manta_trading.cli.commands.data._create_instrument_registry") as mock_factory:
            registry_mock = MagicMock()
            registry_mock.list_instruments.return_value = instruments
            mock_factory.return_value = registry_mock

            result = runner.invoke(app, ["data", "instruments", "list", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["symbol"] == "AAPL"

    def test_venue_filter_passed_to_list_instruments(self):
        with _patch_app(_settings()), \
             patch("manta_trading.cli.commands.data._create_instrument_registry") as mock_factory:
            registry_mock = MagicMock()
            registry_mock.list_instruments.return_value = []
            mock_factory.return_value = registry_mock

            runner.invoke(app, ["data", "instruments", "list", "--venue", "NYSE"])

        registry_mock.list_instruments.assert_called_once_with(
            venue="NYSE", asset_class=None, active_only=True
        )

    def test_asset_class_filter_passed_to_list_instruments(self):
        with _patch_app(_settings()), \
             patch("manta_trading.cli.commands.data._create_instrument_registry") as mock_factory:
            registry_mock = MagicMock()
            registry_mock.list_instruments.return_value = []
            mock_factory.return_value = registry_mock

            runner.invoke(app, ["data", "instruments", "list", "--asset-class", "etf"])

        registry_mock.list_instruments.assert_called_once_with(
            venue=None, asset_class="etf", active_only=True
        )

    def test_missing_timescale_url_exits_with_error(self):
        result = runner.invoke(app, ["data", "instruments", "list"],
                               env={"MT_TIMESCALE_DB_URL": ""})
        # Missing URL causes exit code 1 or error message
        # We test via the helper path rather than env (settings is mocked)
        with _patch_app(_settings(timescale_url=None)):
            result = runner.invoke(app, ["data", "instruments", "list"])
        assert result.exit_code == 1
        assert "MT_TIMESCALE_DB_URL" in result.output


# ---------------------------------------------------------------------------
# TestInstrumentsSeed
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Calendar commands
# ---------------------------------------------------------------------------


class TestCalendarsList:
    """Tests for mt data calendars list."""

    def _mock_calendar_rows(self):
        return [
            {
                "calendar_id": "NYSE",
                "calendar_name": "New York Stock Exchange",
                "timezone": "America/New_York",
                "market_open_time": "09:30:00",
                "market_close_time": "16:00:00",
                "has_extended_hours": True,
                "extended_open_time": "04:00:00",
                "extended_close_time": "20:00:00",
            },
            {
                "calendar_id": "NASDAQ",
                "calendar_name": "NASDAQ",
                "timezone": "America/New_York",
                "market_open_time": "09:30:00",
                "market_close_time": "16:00:00",
                "has_extended_hours": True,
                "extended_open_time": "04:00:00",
                "extended_close_time": "20:00:00",
            },
        ]

    def _run(self, settings, *extra_args):
        with _patch_app(settings), \
             patch("psycopg.connect") as mock_connect:
            cursor_mock = MagicMock()
            cursor_mock.fetchall.return_value = self._mock_calendar_rows()
            cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
            cursor_mock.__exit__ = MagicMock(return_value=False)
            conn_mock = MagicMock()
            conn_mock.cursor.return_value = cursor_mock
            conn_mock.__enter__ = MagicMock(return_value=conn_mock)
            conn_mock.__exit__ = MagicMock(return_value=False)
            mock_connect.return_value = conn_mock
            return runner.invoke(app, ["data", "calendars", "list", *extra_args])

    def test_default_output_shows_calendars(self):
        result = self._run(_settings())
        assert result.exit_code == 0
        assert "NYSE" in result.output
        assert "NASDAQ" in result.output

    def test_json_output_is_valid(self):
        result = self._run(_settings(), "--json")
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["calendar_id"] == "NYSE"

    def test_missing_timescale_url_exits_with_error(self):
        with _patch_app(_settings(timescale_url=None)):
            result = runner.invoke(app, ["data", "calendars", "list"])
        assert result.exit_code == 1
        assert "MT_TIMESCALE_DB_URL" in result.output


class TestCalendarsHolidays:
    """Tests for mt data calendars holidays."""

    def _mock_holidays(self):
        from datetime import time as time_cls
        from manta_trading.data.base.trading_calendar import Holiday, MarketStatus
        return [
            Holiday(
                holiday_date=dt_date(2025, 1, 1),
                holiday_name="New Year's Day",
                market_status=MarketStatus.CLOSED,
            ),
            Holiday(
                holiday_date=dt_date(2025, 11, 28),
                holiday_name="Day after Thanksgiving",
                market_status=MarketStatus.EARLY_CLOSE,
                early_close_time=time_cls(13, 0),
            ),
        ]

    def _patch_calendar(self):
        """Patch TradingCalendar so CLI creates a mock with get_holidays pre-wired."""
        mock_cal = MagicMock()
        mock_cal.get_holidays.return_value = self._mock_holidays()
        return patch(
            "manta_trading.data.base.trading_calendar.TradingCalendar",
            return_value=mock_cal,
        )

    def test_default_output_shows_holidays(self):
        with _patch_app(_settings()), self._patch_calendar():
            result = runner.invoke(
                app, ["data", "calendars", "holidays", "--calendar", "NYSE", "--year", "2025"]
            )
        assert result.exit_code == 0
        assert "New Year" in result.output

    def test_json_output_is_valid(self):
        with _patch_app(_settings()), self._patch_calendar():
            result = runner.invoke(
                app, ["data", "calendars", "holidays", "--calendar", "NYSE", "--year", "2025", "--json"]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["name"] == "New Year's Day"

    def test_missing_timescale_url_exits_with_error(self):
        with _patch_app(_settings(timescale_url=None)):
            result = runner.invoke(
                app, ["data", "calendars", "holidays", "--calendar", "NYSE", "--year", "2025"]
            )
        assert result.exit_code == 1
        assert "MT_TIMESCALE_DB_URL" in result.output
