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

runner = CliRunner()

_END_YEAR = datetime.now().year + TRADING_SESSIONS_EXTENSION_YEARS
_FULL_MAX = date(_END_YEAR, 12, 31)
_TODAY = date.today()
_NEAR_MAX = _TODAY + timedelta(days=45)  # below warn threshold (90 days)

_NYSE_CAL = {
    "calendar_id": "NYSE",
    "timezone": "America/New_York",
    "market_open": __import__("datetime").time(9, 30),
    "market_close": __import__("datetime").time(16, 0),
}


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


def _invoke_extend(*extra_args: str, pool: MagicMock, settings=None) -> object:
    if settings is None:
        settings = _settings()
    with patch("manta_trading.cli.app.Settings", return_value=settings), \
         patch("manta_trading.cli.app.setup_logging"), \
         patch("psycopg_pool.ConnectionPool", return_value=pool):
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

    def _build_pool_near_horizon(self) -> MagicMock:
        """Pool for a calendar whose MAX(session_date) is near today."""
        # conn0: fetch calendars
        conn0 = _conn_with_cursors(_cursor(fetchall=[_NYSE_CAL]))
        # conn1: max_date (near) + holidays
        cur_max = _cursor(fetchone={"max_date": _NEAR_MAX})
        cur_hol = _cursor(fetchall=[])
        conn1 = _conn_with_cursors(cur_max, cur_hol)
        # conn2: upsert (populate_trading_sessions returns rows, execute many)
        conn2 = MagicMock()
        cur_up = _cursor()
        cur_up.rowcount = 10
        conn2.cursor.return_value = cur_up
        # conn3: final max_date check — still near_max (simulating horizon is still short)
        conn3 = _conn_with_cursors(_cursor(fetchone={"max_date": _NEAR_MAX}))
        return _pool_with_conn_seq(conn0, conn1, conn2, conn3)

    def test_strict_exits_4_when_horizon_near(self):
        pool = self._build_pool_near_horizon()
        result = _invoke_extend("--strict", pool=pool)
        assert result.exit_code == 4, f"Output:\n{result.output}"
        assert "NYSE" in result.output
        assert "days remaining" in result.output

    def test_calendar_name_in_warning(self):
        pool = self._build_pool_near_horizon()
        result = _invoke_extend("--strict", pool=pool)
        assert "NYSE" in result.output

    def test_no_strict_exits_0_even_when_horizon_near(self):
        """Without --strict, near-horizon is not an error."""
        pool = self._build_pool_near_horizon()
        result = _invoke_extend(pool=pool)
        assert result.exit_code == 0, f"Output:\n{result.output}"


class TestExtendIdempotent:
    """Re-running a fully extended calendar reports 0 inserted."""

    def _build_pool_full_horizon(self) -> MagicMock:
        # conn0: fetch calendars
        conn0 = _conn_with_cursors(_cursor(fetchall=[_NYSE_CAL]))
        # conn1: max_date = full_max + holidays — start_date > end_date → no upsert
        cur_max = _cursor(fetchone={"max_date": _FULL_MAX})
        cur_hol = _cursor(fetchall=[])
        conn1 = _conn_with_cursors(cur_max, cur_hol)
        # conn2: final max_date check
        conn2 = _conn_with_cursors(_cursor(fetchone={"max_date": _FULL_MAX}))
        return _pool_with_conn_seq(conn0, conn1, conn2)

    def test_zero_inserted_when_already_extended(self):
        pool = self._build_pool_full_horizon()
        result = _invoke_extend(pool=pool)
        assert result.exit_code == 0, f"Output:\n{result.output}"
        assert "0 sessions inserted" in result.output

    def test_strict_exits_0_when_horizon_healthy(self):
        pool = self._build_pool_full_horizon()
        result = _invoke_extend("--strict", pool=pool)
        assert result.exit_code == 0, f"Output:\n{result.output}"
