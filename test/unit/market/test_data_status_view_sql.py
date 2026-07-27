"""Unit tests for _build_data_status_view_sql CTE shape (T11a).

Verifies that the slice-144 rewrite includes the exchange_completed_close
CTE and no longer contains the slice-142 NULL stub. No DB connection needed.
"""

from __future__ import annotations

import pytest

from manta_trading.constants import (
    DAILY_COVERAGE_VIEW,
    LATE_BAR_GRACE_PERIOD,
    MINUTE_COVERAGE_VIEW,
)
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


def _split_bars_summary(sql: str) -> tuple[str, str, str]:
    """Return (prefix, bars_summary_cte, suffix) by paren-matching the CTE."""
    start = sql.index("bars_summary AS (")
    depth = 0
    for index in range(start, len(sql)):
        if sql[index] == "(":
            depth += 1
        elif sql[index] == ")":
            depth -= 1
            if depth == 0:
                return sql[:start], sql[start : index + 1], sql[index + 1 :]
    raise AssertionError("unbalanced parentheses in bars_summary CTE")


class TestDataStatusViewSqlCaggBacked:
    """Slice-167 variant: cagg_backed_bars_summary=True."""

    @pytest.fixture()
    def raw_sql(self) -> str:
        return _build_data_status_view_sql(
            include_daily_branch=True, include_trading_sessions_cte=True
        )

    @pytest.fixture()
    def sql(self) -> str:
        return _build_data_status_view_sql(
            include_daily_branch=True,
            include_trading_sessions_cte=True,
            cagg_backed_bars_summary=True,
        )

    def test_bars_summary_reads_coverage_caggs(self, sql: str) -> None:
        _, cte, _ = _split_bars_summary(sql)
        assert MINUTE_COVERAGE_VIEW in cte
        assert DAILY_COVERAGE_VIEW in cte

    def test_bars_summary_does_not_scan_raw_hypertables(self, sql: str) -> None:
        """The whole point of the slice: no per-symbol raw aggregate remains."""
        _, cte, _ = _split_bars_summary(sql)
        assert "FROM minute_ohlcv" not in cte
        assert "FROM daily_ohlcv" not in cte

    def test_view_names_come_from_constants(self, sql: str) -> None:
        """Guards the no-magic-strings rule: renaming the constant must move
        the rendered SQL with it, so a literal would fail this."""
        assert f"FROM {MINUTE_COVERAGE_VIEW}" in sql
        assert f"FROM {DAILY_COVERAGE_VIEW}" in sql

    def test_differs_from_raw_variant_only_inside_bars_summary(
        self, sql: str, raw_sql: str
    ) -> None:
        """Mechanically pins the D2 contract.

        Everything outside the bars_summary CTE -- symbols_x_granularity,
        gap_counts, exchange_completed_close, the health CASE, every join and
        every projected column -- must be byte-identical to the raw variant.
        """
        raw_prefix, raw_cte, raw_suffix = _split_bars_summary(raw_sql)
        cagg_prefix, cagg_cte, cagg_suffix = _split_bars_summary(sql)

        assert cagg_prefix == raw_prefix
        assert cagg_suffix == raw_suffix
        assert cagg_cte != raw_cte

    def test_output_column_list_is_unchanged(self, sql: str, raw_sql: str) -> None:
        """The projected column list and order are the D2 contract itself."""
        raw_select = raw_sql[raw_sql.rindex("SELECT s.symbol") :]
        cagg_select = sql[sql.rindex("SELECT s.symbol") :]
        assert cagg_select == raw_select

    def test_minute_branch_uses_summable_bar_counts(self, sql: str) -> None:
        """``SUM(bars)``, not ``COUNT(*)``.

        A ``COUNT(*)`` here would count coverage rows (one per symbol-year)
        rather than bars, silently reporting ~22 instead of millions.
        """
        _, cte, _ = _split_bars_summary(sql)
        assert "SUM(bars)" in cte
        assert "COUNT(*)" not in cte

    def test_bars_stored_keeps_its_bigint_type(self, sql: str) -> None:
        """``SUM()`` over bigint returns numeric -- it must be cast back.

        Two things break without the cast: ``CREATE OR REPLACE VIEW`` refuses
        outright ("cannot change data type of view column bars_stored from
        bigint to numeric"), and the D2 column contract would silently change
        type for every downstream reader.
        """
        _, cte, _ = _split_bars_summary(sql)
        assert "SUM(bars)::BIGINT" in cte
        # No bare SUM(bars) may survive without the cast.
        assert cte.count("SUM(bars)") == cte.count("SUM(bars)::BIGINT")

    def test_without_daily_branch_omits_daily_coverage(self) -> None:
        sql = _build_data_status_view_sql(
            include_daily_branch=False,
            include_trading_sessions_cte=True,
            cagg_backed_bars_summary=True,
        )
        _, cte, _ = _split_bars_summary(sql)
        assert MINUTE_COVERAGE_VIEW in cte
        assert DAILY_COVERAGE_VIEW not in cte

    def test_default_variant_is_still_raw_backed(self) -> None:
        """The flag must default off, so existing migrations are untouched."""
        sql = _build_data_status_view_sql(
            include_daily_branch=True, include_trading_sessions_cte=True
        )
        _, cte, _ = _split_bars_summary(sql)
        assert "FROM minute_ohlcv" in cte
        assert MINUTE_COVERAGE_VIEW not in cte


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
