"""Unit tests for ``mt data extend`` CLI command (T10).

Tests strict-mode exit behavior, idempotent re-run reporting, and
successful extension — all without hitting a real DB.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.constants import (
    TRADING_SESSIONS_EXTENSION_YEARS,
    TRADING_SESSIONS_HORIZON_WARN_DAYS,
)
from manta_trading.data.base.session_extension import CalendarExtension

runner = CliRunner()

_END_YEAR = datetime.now().year + TRADING_SESSIONS_EXTENSION_YEARS
_FULL_MAX = date(_END_YEAR, 12, 31)
_TODAY = date.today()
_NEAR_MAX = _TODAY + timedelta(days=45)  # below warn threshold (90 days)

_EXT = "manta_trading.data.base.session_extension"


def _extension(
    horizon: date, *, rows: int = 10, bound: date = _FULL_MAX, clamped: bool = False
) -> CalendarExtension:
    return CalendarExtension(
        calendar_id="NYSE",
        rows_upserted=rows,
        horizon_after=horizon,
        holidays_seeded_through=bound,
        clamped_by_holiday_bound=clamped,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(timescale_url: str | None = "postgresql://ts/db"):
    s = MagicMock()
    s.timescale_db_url = timescale_url
    s.market_db_url = None
    return s


def _ctx_mgr(obj: MagicMock) -> MagicMock:
    """Wrap obj in a minimal context manager mock."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=obj)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _cursor(fetchone=None, fetchall=None) -> MagicMock:
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    if fetchone is not None:
        cur.fetchone.return_value = fetchone
    if fetchall is not None:
        cur.fetchall.return_value = fetchall
    return cur


def _conn_with_cursors(*cursors) -> MagicMock:
    """Return a connection mock whose .cursor() cycles through provided cursors."""
    conn = MagicMock()
    it = iter(cursors)
    conn.cursor.side_effect = lambda **kw: next(it)
    return conn


def _pool_with_conn_seq(*conn_mocks) -> MagicMock:
    """Pool whose .connection() cycles through conn_mocks wrapped as context managers."""
    pool = MagicMock()
    pool.__enter__ = MagicMock(return_value=pool)
    pool.__exit__ = MagicMock(return_value=False)
    it = iter([_ctx_mgr(c) for c in conn_mocks])
    pool.connection.side_effect = lambda **kw: next(it)
    return pool


def _pool_for_nyse() -> MagicMock:
    """Pool: first connection lists calendars (NYSE), second extends it."""
    conn0 = _conn_with_cursors(_cursor(fetchall=[("NYSE",)]))
    return _pool_with_conn_seq(conn0, MagicMock())


def _invoke_extend(
    *extra_args: str, extension: CalendarExtension, settings=None
) -> object:
    if settings is None:
        settings = _settings()
    with patch("manta_trading.cli.app.Settings", return_value=settings), \
         patch("manta_trading.cli.app.setup_logging"), \
         patch("psycopg_pool.ConnectionPool", return_value=_pool_for_nyse()), \
         patch(f"{_EXT}.session_horizon", return_value=extension.horizon_after), \
         patch(f"{_EXT}.extend_calendar_sessions", return_value=extension):
        return runner.invoke(app, ["data", "extend", *extra_args])


# ---------------------------------------------------------------------------
# Tests: help text
# ---------------------------------------------------------------------------

class TestExtendHelp:
    def test_extend_appears_in_data_help(self):
        result = runner.invoke(app, ["data", "--help"])
        assert result.exit_code == 0
        assert "extend" in result.output

    def test_extend_help_shows_options(self):
        result = runner.invoke(app, ["data", "extend", "--help"])
        assert result.exit_code == 0
        assert "--calendar" in result.output
        assert "--strict" in result.output


# ---------------------------------------------------------------------------
# Tests: missing URL
# ---------------------------------------------------------------------------

class TestExtendMissingUrl:
    def test_exits_1_when_no_timescale_url(self):
        settings = _settings(timescale_url=None)
        with patch("manta_trading.cli.app.Settings", return_value=settings), \
             patch("manta_trading.cli.app.setup_logging"):
            result = runner.invoke(app, ["data", "extend"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Tests: strict mode
# ---------------------------------------------------------------------------

class TestExtendStrict:
    """Strict mode exits 4 when horizon is below warn threshold."""

    def test_strict_exits_4_when_horizon_near(self):
        result = _invoke_extend("--strict", extension=_extension(_NEAR_MAX))
        assert result.exit_code == 4, f"Output:\n{result.output}"
        assert "NYSE" in result.output
        assert "days remaining" in result.output

    def test_no_strict_exits_0_even_when_horizon_near(self):
        """Without --strict, near-horizon is not an error."""
        result = _invoke_extend(extension=_extension(_NEAR_MAX))
        assert result.exit_code == 0, f"Output:\n{result.output}"


class TestExtendIdempotent:
    """Re-running a fully extended calendar reports 0 inserted."""

    def test_zero_inserted_when_already_extended(self):
        result = _invoke_extend(extension=_extension(_FULL_MAX, rows=0))
        assert result.exit_code == 0, f"Output:\n{result.output}"
        assert "0 sessions inserted" in result.output

    def test_strict_exits_0_when_horizon_healthy(self):
        result = _invoke_extend("--strict", extension=_extension(_FULL_MAX, rows=0))
        assert result.exit_code == 0, f"Output:\n{result.output}"


class TestExtendHolidayBound:
    """Each calendar's line names its holiday bound; a clamp names the fix."""

    def test_unclamped_line_names_bound(self):
        result = _invoke_extend(extension=_extension(_FULL_MAX))
        assert f"holidays seeded through {_FULL_MAX}" in result.output
        assert "seed the next year" not in result.output

    def test_clamped_line_prints_bound_message(self):
        bound = _TODAY + timedelta(days=45)
        result = _invoke_extend(
            "--strict", extension=_extension(bound, bound=bound, clamped=True)
        )
        assert result.exit_code == 4, f"Output:\n{result.output}"
        # Rich wraps at the terminal width; compare with whitespace collapsed.
        output = " ".join(result.output.split())
        assert f"holidays seeded through {bound}" in output
        assert "seed the next year's NYSE schedule" in output
