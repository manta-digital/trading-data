"""Unit tests for pick_most_recent_actionable_gap."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from manta_trading.data.gaps.actionable_gap_selector import (
    GapRow,
    pick_most_recent_actionable_gap,
)
from manta_trading.data.quality.fetch_status import FetchStatus

UTC = timezone.utc


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, 0, tzinfo=UTC)


def _make_conn(return_row: tuple | None) -> MagicMock:
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = return_row
    conn.cursor.return_value = cur
    return conn


class TestPickMostRecentActionableGap:
    def test_returns_none_when_no_rows(self) -> None:
        conn = _make_conn(None)
        result = pick_most_recent_actionable_gap(
            conn, "AAPL", "daily", _dt(2024, 1, 1), _dt(2024, 12, 31)
        )
        assert result is None

    def test_returns_gap_row_for_unknown_status(self) -> None:
        row = ("AAPL", "daily", _dt(2024, 6, 1), _dt(2024, 6, 30), "UNKNOWN", None, 1)
        conn = _make_conn(row)
        result = pick_most_recent_actionable_gap(
            conn, "AAPL", "daily", _dt(2024, 1, 1), _dt(2024, 12, 31)
        )
        assert result is not None
        assert result.symbol == "AAPL"
        assert result.fetch_status == "UNKNOWN"
        assert result.gap_end == _dt(2024, 6, 30)

    def test_returns_gap_row_for_failed_retryable_status(self) -> None:
        row = ("AAPL", "daily", _dt(2024, 3, 1), _dt(2024, 3, 31), "FAILED_RETRYABLE", _dt(2024, 4, 1), 2)
        conn = _make_conn(row)
        result = pick_most_recent_actionable_gap(
            conn, "AAPL", "daily", _dt(2024, 1, 1), _dt(2024, 12, 31)
        )
        assert result is not None
        assert result.fetch_status == "FAILED_RETRYABLE"
        assert result.attempt_count == 2

    def test_query_filters_to_actionable_statuses_only(self) -> None:
        """SQL IN clause must include UNKNOWN and FAILED_RETRYABLE, not terminal ones."""
        conn = _make_conn(None)
        pick_most_recent_actionable_gap(
            conn, "AAPL", "daily", _dt(2024, 1, 1), _dt(2024, 12, 31)
        )
        cur = conn.cursor.return_value.__enter__.return_value
        call_args = cur.execute.call_args
        sql: str = call_args[0][0]
        params: tuple = call_args[0][1]

        assert "fetch_status = ANY" in sql
        assert "ORDER BY gap_end DESC" in sql
        assert "LIMIT 1" in sql
        # Params should include both actionable statuses
        params_str = str(params)
        assert "UNKNOWN" in params_str
        assert "FAILED_RETRYABLE" in params_str
        assert "PROVIDER_HOLE" not in params_str
        assert "RETRY_EXHAUSTED" not in params_str

    def test_query_scopes_to_window(self) -> None:
        conn = _make_conn(None)
        from_ts = _dt(2024, 1, 1)
        to_ts = _dt(2024, 12, 31)
        pick_most_recent_actionable_gap(conn, "MSFT", "minute", from_ts, to_ts)
        cur = conn.cursor.return_value.__enter__.return_value
        params = cur.execute.call_args[0][1]
        assert "MSFT" in params
        assert "minute" in params
        assert from_ts in params
        assert to_ts in params
