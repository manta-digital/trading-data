"""Unit tests for _build_data_status_view_sql CTE shape (T11a).

Verifies that the slice-144 rewrite includes the exchange_completed_close
CTE and no longer contains the slice-142 NULL stub. No DB connection needed.
"""

from __future__ import annotations

import pytest

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    DAILY_COVERAGE_VIEW,
    LATE_BAR_GRACE_PERIOD,
    MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_VIEW,
)
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.market.schema.migrations.minute import (
    _build_data_status_view_sql,
    _composite_lag_literal,
    _data_status_doc_comment,
    _interval_literal,
    _open_gap_predicate,
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


class TestDataStatusDocCommentCaggLag:
    """Slice 169 C.10 / criterion 14: the CAGG LAG bound must include the
    bucket-width term.

    Before slice 169 this clause derived the bound from the refresh *schedule
    intervals* alone -- "2 hours total" -- which was wrong from slice 167
    onward, independent of any width change. A refresh policy's window is
    truncated to whole buckets, so the open bucket is never re-materialized
    while open; the schedule interval bounds only how promptly *closed* buckets
    are rewritten. An operator running ``\\d+ data_status`` read a promise of
    hours against a production reality of months.

    These assertions are pinned to the constants, never to a literal width, so
    they keep holding when ``COVERAGE_BUCKET_INTERVAL`` changes again.
    """

    @pytest.fixture()
    def comment(self) -> str:
        # Doubled quotes are the DO-block escaping; unwrap for plain matching.
        return _data_status_doc_comment().replace("''", "'")

    def test_states_the_bucket_width_term(self, comment: str) -> None:
        """The bound must name one coverage bucket, rendered from the constant."""
        assert _interval_literal(COVERAGE_BUCKET_INTERVAL) in comment

    def test_minute_total_includes_bucket_plus_both_hops(self, comment: str) -> None:
        expected = _composite_lag_literal(
            COVERAGE_BUCKET_INTERVAL
            + MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL
            + MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL
        )
        assert f"{expected} total" in comment

    def test_daily_total_includes_bucket_plus_one_hop(self, comment: str) -> None:
        expected = _composite_lag_literal(
            COVERAGE_BUCKET_INTERVAL + DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL
        )
        assert f"{expected} total" in comment

    def test_open_bucket_is_named_as_the_reason(self, comment: str) -> None:
        """The comment must say *why*, not just carry a bigger number.

        A future reader who sees only a widened bound learns nothing about the
        open-bucket mechanism, which is the whole content of the correction.
        """
        assert "open" in comment.lower()
        assert "truncated to whole buckets" in comment

    def test_two_hop_only_phrasing_is_gone(self, comment: str) -> None:
        """Regression guard: the pre-169 formula must not come back.

        Computed from the constants rather than spelled "2 hours total", so
        this keeps guarding if the schedule intervals are ever retuned.
        """
        two_hop_only = _composite_lag_literal(
            MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL
            + MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL
        )
        # The two-hop figure must never stand as a *whole* bound. Matching on
        # "(<two_hop> total)" rather than the bare substring is deliberate: the
        # corrected minute bound legitimately ends in the same minute count
        # ("365 days 120 minutes total"), so a bare-substring check would fail
        # on correct output and pass only by accident of formatting.
        assert f"({two_hop_only} total)" not in comment
        assert "at most the two-hop refresh interval" not in comment


# ---------------------------------------------------------------------------
# Slice 922: open gaps and the walk anchor
# ---------------------------------------------------------------------------


class TestOpenGapsAndWalkAnchor:
    """The two meanings migration 056 changed, asserted on the rendered SQL.

    Both are the kind of change that is invisible until an operator reads a
    number and believes it, so the text that produces them is pinned here:
    the values come from ``FetchStatus``, and the STALE test is a join to the
    anchor rather than an interval.
    """

    @pytest.fixture()
    def sql(self) -> str:
        return _build_data_status_view_sql(
            include_daily_branch=True,
            include_trading_sessions_cte=True,
            cagg_backed_bars_summary=True,
        )

    def test_gap_count_filters_to_the_open_statuses(self, sql: str) -> None:
        assert "COUNT(*) FILTER (WHERE fetch_status IN (" in sql
        assert f"''{FetchStatus.UNKNOWN.value}''" in sql
        assert f"''{FetchStatus.FAILED_RETRYABLE.value}''" in sql

    def test_a_terminal_hole_is_not_counted_as_a_gap(self, sql: str) -> None:
        """PROVIDER_HOLE is the provider's answer, not an open question."""
        predicate = _open_gap_predicate()
        assert FetchStatus.PROVIDER_HOLE.value not in predicate

    def test_retry_exhausted_is_not_counted_as_a_gap(self, sql: str) -> None:
        """It is terminal, and already carried by has_retry_exhausted."""
        assert FetchStatus.RETRY_EXHAUSTED.value not in _open_gap_predicate()

    def test_retry_exhausted_still_drives_the_failed_verdict(self, sql: str) -> None:
        assert f"BOOL_OR(fetch_status = ''{FetchStatus.RETRY_EXHAUSTED.value}'')" in sql
        assert "has_retry_exhausted" in sql

    def test_the_predicate_names_only_enum_values(self) -> None:
        import re

        quoted = set(re.findall(r"''([^']+)''", _open_gap_predicate()))
        assert quoted <= {status.value for status in FetchStatus}
        assert quoted == {
            FetchStatus.UNKNOWN.value,
            FetchStatus.FAILED_RETRYABLE.value,
        }

    def test_the_walk_anchor_cte_reads_ended_anchored_runs(self, sql: str) -> None:
        assert "walk_anchor AS (" in sql
        assert "MAX(walk_anchor_at) AS anchor FROM pass_runs" in sql
        assert "walk_anchor_at IS NOT NULL AND ended_at IS NOT NULL" in sql
        assert "GROUP BY pass" in sql

    def test_the_anchor_joins_on_granularity(self, sql: str) -> None:
        """A minute pass anchors minute symbols, a daily pass daily ones."""
        assert "LEFT JOIN walk_anchor wa ON wa.pass = s.granularity" in sql

    def test_stale_compares_against_the_anchor(self, sql: str) -> None:
        assert "ast.last_attempt_ts < wa.anchor THEN ''STALE''" in sql

    def test_stale_no_longer_uses_a_fixed_interval(self, sql: str) -> None:
        """The thresholds this replaced called every minute symbol STALE six
        days a week under a weekly collecting cadence."""
        stale_clause = sql[sql.index("THEN ''FAILED''") : sql.index("''GAPS''")]
        assert "NOW() -" not in stale_clause
        assert "INTERVAL" not in stale_clause

    def test_a_never_attempted_symbol_is_still_stale(self, sql: str) -> None:
        """With a NULL anchor this is the only STALE case left."""
        assert "ast.last_attempt_ts IS NULL" in sql

    def test_the_late_bar_grace_literal_survives(self, sql: str) -> None:
        """_interval_literal keeps a consumer: the exchange-close grace."""
        assert _interval_literal(LATE_BAR_GRACE_PERIOD) in sql


class TestDocCommentStatesTheNewRules:
    """The comment is what ``\\d+ data_status`` shows; it must not lie."""

    @pytest.fixture()
    def comment(self) -> str:
        return _data_status_doc_comment()

    def test_it_states_the_anchor_rule(self, comment: str) -> None:
        assert "STALE MEANS NOT ATTEMPTED IN THE LAST RECORDED WALK" in comment
        assert "walk_anchor_at" in comment

    def test_it_states_the_null_anchor_behaviour(self, comment: str) -> None:
        assert "only never-attempted symbols read STALE" in comment

    def test_it_explains_that_an_aborted_pass_still_moves_the_anchor(
        self, comment: str
    ) -> None:
        assert "aborted" in comment

    def test_it_states_the_open_gap_meaning(self, comment: str) -> None:
        assert "GAP_COUNT MEANS OPEN GAPS" in comment
        assert FetchStatus.UNKNOWN.value in comment
        assert FetchStatus.FAILED_RETRYABLE.value in comment

    def test_it_names_the_terminal_statuses_it_excludes(self, comment: str) -> None:
        assert FetchStatus.PROVIDER_HOLE.value in comment
        assert FetchStatus.RETRY_EXHAUSTED.value in comment
