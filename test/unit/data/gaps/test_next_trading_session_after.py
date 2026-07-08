"""Unit tests for next_trading_session_after."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from manta_trading.data.gaps.next_trading_session_after import next_trading_session_after


def _make_conn(return_row: tuple | None) -> MagicMock:
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = return_row
    conn.cursor.return_value = cur
    return conn


class TestNextTradingSessionAfter:
    def test_returns_next_date(self) -> None:
        conn = _make_conn((date(2024, 11, 25),))
        result = next_trading_session_after(conn, "US", date(2024, 11, 22))
        assert result == date(2024, 11, 25)

    def test_returns_none_past_horizon(self) -> None:
        conn = _make_conn((None,))
        result = next_trading_session_after(conn, "US", date(2099, 12, 31))
        assert result is None

    def test_returns_none_when_no_row(self) -> None:
        conn = _make_conn(None)
        result = next_trading_session_after(conn, "US", date(2024, 11, 22))
        assert result is None

    def test_query_uses_calendar_id_and_after_date(self) -> None:
        conn = _make_conn((date(2024, 11, 26),))
        next_trading_session_after(conn, "US", date(2024, 11, 29))
        cur = conn.cursor.return_value.__enter__.return_value
        call = cur.execute.call_args
        sql: str = call[0][0]
        params = call[0][1]
        assert "trading_sessions" in sql
        assert "session_date > %s" in sql
        assert params[0] == "US"
        assert params[1] == date(2024, 11, 29)
