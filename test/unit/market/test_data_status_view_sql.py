"""Unit tests for _build_data_status_view_sql CTE shape (T11a).

Verifies that the slice-144 rewrite includes the exchange_completed_close
CTE and no longer contains the slice-142 NULL stub. No DB connection needed.
"""

from __future__ import annotations

import pytest

from manta_trading.constants import LATE_BAR_GRACE_PERIOD
from manta_trading.market.schema.migrations.minute import (
    _build_data_status_view_sql,
    _interval_literal,
)


class TestDataStatusViewSqlWithTradingSessions:
    """Slice-144 variant: include_trading_sessions_cte=True."""

    @pytest.fixture()
    def sql(self) -> str:
        return _build_data_status_view_sql(
            include_daily_branch=True, include_trading_sessions_cte=True
        )

    def test_contains_exchange_completed_close_cte(self, sql: str) -> None:
        assert "exchange_completed_close" in sql

    def test_contains_session_close_utc(self, sql: str) -> None:
        assert "session_close_utc" in sql

    def test_does_not_contain_null_stub(self, sql: str) -> None:
        assert "NULL::TIMESTAMPTZ AS target_end_ts" not in sql

    def test_grace_period_literal_in_cte(self, sql: str) -> None:
        grace_literal = _interval_literal(LATE_BAR_GRACE_PERIOD)
        assert grace_literal in sql

    def test_target_end_ts_from_completed_close(self, sql: str) -> None:
        assert "completed_close_ts AS target_end_ts" in sql

    def test_left_join_on_calendar_id(self, sql: str) -> None:
        assert "LEFT JOIN exchange_completed_close ec" in sql
        assert "ec.calendar_id = s.trading_calendar_id" in sql

    def test_without_daily_also_contains_cte(self) -> None:
        sql = _build_data_status_view_sql(
            include_daily_branch=False, include_trading_sessions_cte=True
        )
        assert "exchange_completed_close" in sql
        assert "NULL::TIMESTAMPTZ AS target_end_ts" not in sql


class TestDataStatusViewSqlWithoutTradingSessions:
    """Slice-142/143 stub variant: include_trading_sessions_cte=False (default)."""

    @pytest.fixture()
    def sql(self) -> str:
        return _build_data_status_view_sql(include_daily_branch=True)

    def test_contains_null_stub(self, sql: str) -> None:
        assert "NULL::TIMESTAMPTZ AS target_end_ts" in sql

    def test_does_not_contain_trading_sessions_cte(self, sql: str) -> None:
        assert "exchange_completed_close" not in sql

    def test_does_not_contain_session_close_utc(self, sql: str) -> None:
        assert "session_close_utc" not in sql
