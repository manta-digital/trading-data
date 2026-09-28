"""Unit tests for ``mt data calendars sessions`` (221 D9). No DB."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from typer.testing import CliRunner, Result

from manta_trading.cli.app import app
from manta_trading.data.base.session_index import Session
from manta_trading.data.base.trading_calendar import (
    CalendarNotFoundError,
    Holiday,
    MarketStatus,
)

runner = CliRunner()

_CT = ZoneInfo("America/Chicago")
_MOD = "manta_trading.cli.commands.calendar_sessions"


def _utc(*args: int) -> datetime:
    year, month, day, hour, minute = (*args, 0)[:5]
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# Christmas 2024: 12-24 closes early, 12-25 is closed, 12-26 opens on the 25th.
_SESSIONS = [
    Session(
        "CME_EQUITY", date(2024, 12, 23), _utc(2024, 12, 22, 23), _utc(2024, 12, 23, 22)
    ),
    Session(
        "CME_EQUITY",
        date(2024, 12, 24),
        _utc(2024, 12, 23, 23),
        _utc(2024, 12, 24, 18, 15),
    ),
    Session(
        "CME_EQUITY", date(2024, 12, 26), _utc(2024, 12, 25, 23), _utc(2024, 12, 26, 22)
    ),
]
_HOLIDAYS = [
    Holiday(date(2024, 12, 24), "Christmas Eve", MarketStatus.EARLY_CLOSE),
    Holiday(date(2024, 12, 25), "Christmas Day", MarketStatus.CLOSED),
]


_LAST_CLOSE = _utc(2026, 12, 31, 22)


def _calendar(last_close: datetime = _LAST_CLOSE) -> MagicMock:
    cal = MagicMock()
    cal.calendar_id = "CME_EQUITY"
    cal.timezone = _CT
    cal.get_holidays.return_value = _HOLIDAYS
    cal.populated_span.return_value = (_utc(2020, 1, 1, 23), last_close)
    cal.sessions_between.return_value = _SESSIONS
    return cal


def _invoke(cal: MagicMock, *args: str) -> Result:
    settings = MagicMock()
    settings.timescale_db_url = "postgresql://test/db"
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch(f"{_MOD}.TradingCalendar", return_value=cal),
    ):
        return runner.invoke(
            app,
            ["data", "calendars", "sessions", "--calendar", "CME_EQUITY", *args],
            env={"COLUMNS": "200"},
        )


def test_json_output_rows() -> None:
    result = _invoke(
        _calendar(), "--from", "2024-12-23", "--to", "2024-12-26", "--json"
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["session_date"] for r in rows] == [
        "2024-12-23",
        "2024-12-24",
        "2024-12-26",
    ]
    eve = rows[1]
    assert eve["open_local"] == "2024-12-23T17:00:00-06:00"
    assert eve["close_local"] == "2024-12-24T12:15:00-06:00"
    assert eve["close_utc"] == "2024-12-24T18:15:00+00:00"
    assert eve["duration"] == "19h15m"
    assert eve["exception"] == "Christmas Eve"
    assert rows[0]["exception"] is None


def test_table_output() -> None:
    result = _invoke(_calendar(), "--from", "2024-12-23", "--to", "2024-12-26")
    assert result.exit_code == 0, result.output
    assert "Christmas Eve" in result.output
    assert "12-23 17:00" in result.output  # 12-24 opens the evening before
    assert "3 session(s)" in result.output


def test_filters_to_requested_dates() -> None:
    result = _invoke(
        _calendar(), "--from", "2024-12-24", "--to", "2024-12-24", "--json"
    )
    assert [r["session_date"] for r in json.loads(result.output)] == ["2024-12-24"]


def test_window_is_clamped_to_last_close() -> None:
    """Asking through the last populated day does not overrun the span."""
    cal = _calendar(last_close=_utc(2024, 12, 26, 22))
    result = _invoke(cal, "--from", "2024-12-23", "--to", "2024-12-26", "--json")
    assert result.exit_code == 0, result.output
    assert cal.sessions_between.call_args.args[1] == _utc(2024, 12, 26, 22)


def test_beyond_horizon_exits_1_with_message() -> None:
    cal = _calendar(last_close=_utc(2024, 12, 24, 18, 15))
    result = _invoke(cal, "--from", "2024-12-23", "--to", "2024-12-26")
    assert result.exit_code == 1
    assert "mt data extend" in " ".join(result.output.split())
    cal.sessions_between.assert_not_called()


def test_unknown_calendar_exits_1_with_message() -> None:
    cal = _calendar()
    cal.get_holidays.side_effect = CalendarNotFoundError("NOPE")
    result = _invoke(cal, "--from", "2024-12-23", "--to", "2024-12-26")
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "'NOPE' not found" in " ".join(result.output.split())
    cal.close.assert_called_once()
