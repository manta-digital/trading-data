"""``selection_sql`` clause structure (slice 264, Task 4.1b).

Five settings, each independently omittable: cheap to prove without a
database. Row outcomes — which market is selected — are the integration
tier's job (``test_kalshi_candles.py``); this proves *which clauses are
present and how they are spelled*, including the NULL asymmetry Task 4.1
makes deliberate. Assertions work on the ``Composed`` sequence, never on the
whole statement string.
"""

from __future__ import annotations

import pytest
from psycopg import sql

from manta_trading.data.kalshi.candle_selection import MARKET_JOIN
from manta_trading.data.kalshi.selection import (
    CATALOG_JOIN,
    CollectionRule,
    Selection,
    SelectionForm,
    describe_trades_filter,
    selection_sql,
    trades_filter_sql,
)

EMPTY = CollectionRule(
    traded_only=False,
    categories=frozenset(),
    excluded_categories=frozenset(),
    excluded_series_pattern=None,
    excluded_title_pattern=None,
)
RULE_C = CollectionRule(
    traded_only=True,
    categories=frozenset(),
    excluded_categories=frozenset({"Sports", "Mentions"}),
    excluded_series_pattern=r"MENTION|SAY",
    excluded_title_pattern=r"\m(say|says|mention|mentions)\M",
)


def _walk(composable: sql.Composable) -> list[sql.Composable]:
    if isinstance(composable, sql.Composed):
        return [leaf for part in composable for leaf in _walk(part)]
    return [composable]


def fragments(selection: Selection) -> list[str]:
    """The literal SQL fragments of the predicate, in order (nested
    ``Composed`` flattened; identifiers and placeholders skipped)."""
    return [
        leaf._obj  # pyright: ignore[reportPrivateUsage]
        for leaf in _walk(selection.predicate)
        if isinstance(leaf, sql.SQL)
    ]


def text(selection: Selection) -> str:
    return "".join(fragments(selection))


class TestOmission:
    def test_every_setting_empty_is_always_true_with_no_params(self):
        selection = selection_sql(EMPTY, "recent")
        assert selection.params == {}
        assert text(selection) == "(TRUE)"

    def test_allow_list_alone(self):
        rule = CollectionRule(False, frozenset({"Sports"}), frozenset(), None, None)
        selection = selection_sql(rule, "recent")
        assert list(selection.params) == ["collection_categories"]
        assert selection.params["collection_categories"] == ["Sports"]
        assert "s.category = ANY(%(collection_categories)s)" in text(selection)

    def test_exclude_list_alone(self):
        rule = CollectionRule(False, frozenset(), frozenset({"Sports"}), None, None)
        selection = selection_sql(rule, "recent")
        assert list(selection.params) == ["collection_excluded_categories"]

    def test_series_pattern_alone(self):
        rule = CollectionRule(False, frozenset(), frozenset(), "MENTION", None)
        selection = selection_sql(rule, "recent")
        assert list(selection.params) == ["collection_excluded_series_pattern"]
        assert selection.params["collection_excluded_series_pattern"] == "MENTION"

    def test_title_pattern_alone(self):
        rule = CollectionRule(False, frozenset(), frozenset(), None, "say")
        selection = selection_sql(rule, "recent")
        assert list(selection.params) == ["collection_excluded_title_pattern"]

    @pytest.mark.parametrize(
        ("form", "column"), [("recent", "volume_24h_fp"), ("ever", "volume_fp")]
    )
    def test_traded_alone_binds_no_value_and_picks_the_form_column(
        self, form: SelectionForm, column: str
    ):
        rule = CollectionRule(True, frozenset(), frozenset(), None, None)
        selection = selection_sql(rule, form)
        assert selection.params == {}
        identifiers = [
            leaf
            for leaf in _walk(selection.predicate)
            if isinstance(leaf, sql.Identifier)
        ]
        assert len(identifiers) == 1
        assert identifiers[0] == sql.Identifier(column)

    def test_any_form_omits_the_traded_clause(self):
        """Task 1.3 / 265 Decision 3: a trade is proof of trading, so the
        trades write path renders no volume test — not a third column."""
        rule = CollectionRule(True, frozenset(), frozenset(), None, None)
        selection = selection_sql(rule, "any")
        assert selection.params == {}
        assert text(selection) == "(TRUE)"
        assert "volume" not in text(selection_sql(RULE_C, "any"))
        assert not [
            leaf
            for leaf in _walk(selection_sql(RULE_C, "any").predicate)
            if isinstance(leaf, sql.Identifier)
        ]

    @pytest.mark.parametrize(
        ("form", "column"), [("recent", "volume_24h_fp"), ("ever", "volume_fp")]
    )
    def test_recent_and_ever_still_carry_the_traded_clause(
        self, form: SelectionForm, column: str
    ):
        selection = selection_sql(RULE_C, form)
        assert sql.Identifier(column) in _walk(selection.predicate)
        assert text(selection).count(" AND ") == 3

    def test_rule_c_default(self):
        selection = selection_sql(RULE_C, "recent")
        assert list(selection.params) == [
            "collection_excluded_categories",
            "collection_excluded_series_pattern",
            "collection_excluded_title_pattern",
        ]
        # Category sets are bound sorted, so equal rules bind equal params.
        assert selection.params["collection_excluded_categories"] == [
            "Mentions",
            "Sports",
        ]
        assert text(selection).count(" AND ") == 3


class TestNullAsymmetry:
    """Task 4.1 / review F003: exclusions keep an uncategorised or untitled
    series; the allow-list deliberately does not match one."""

    def test_exclusion_clauses_use_coalesce(self):
        selection = selection_sql(RULE_C, "recent")
        body = text(selection)
        assert (
            "COALESCE(s.category, '') <> ALL(%(collection_excluded_categories)s)"
            in body
        )
        assert "COALESCE(s.title, '') !~* %(collection_excluded_title_pattern)s" in body
        # ``series.ticker`` is the primary key: no COALESCE needed or used.
        assert "s.ticker !~ %(collection_excluded_series_pattern)s" in body

    def test_allow_list_is_not_wrapped_in_coalesce(self):
        rule = CollectionRule(False, frozenset({"Sports"}), frozenset(), None, None)
        body = text(selection_sql(rule, "recent"))
        assert "s.category = ANY(%(collection_categories)s)" in body
        assert "COALESCE(s.category" not in body

    def test_every_value_is_a_bound_parameter(self):
        """The operator's regex must never reach the SQL text."""
        values = ("Zq-cat", "Zq-excl", "Zq-series-re", "Zq-title-re")
        rule = CollectionRule(
            True, frozenset({values[0]}), frozenset({values[1]}), values[2], values[3]
        )
        selection = selection_sql(rule, "ever")
        body = text(selection)
        for value in values:
            assert value not in body
        assert set(selection.params) == {
            "collection_categories",
            "collection_excluded_categories",
            "collection_excluded_series_pattern",
            "collection_excluded_title_pattern",
        }


class TestTradesFilterSql:
    """Slice 268, Task 2.2: the trades-tape filter's membership test."""

    def test_empty_set_is_false_with_no_params(self):
        selection = trades_filter_sql(frozenset())
        assert selection.params == {}
        assert text(selection) == "FALSE"

    def test_membership_sql_with_sorted_bound_list(self):
        selection = trades_filter_sql(frozenset({"Sports", "Crypto"}))
        assert text(selection) == (
            "COALESCE(s.category, '') = ANY(%(trades_excluded_categories)s)"
        )
        assert selection.params == {
            "trades_excluded_categories": ["Crypto", "Sports"]
        }

    def test_category_values_never_in_statement_text(self):
        selection = trades_filter_sql(frozenset({"Zq-filtered"}))
        assert "Zq-filtered" not in text(selection)

    def test_parameter_names_disjoint_from_selection_sql(self):
        """Rule and filter must bind together in one statement — no
        parameter name may collide with any ``selection_sql`` can emit."""
        rule_params = set(selection_sql(RULE_C, "any").params) | set(
            selection_sql(
                CollectionRule(True, frozenset({"A"}), frozenset({"B"}), "x", "y"),
                "ever",
            ).params
        )
        filter_params = set(trades_filter_sql(frozenset({"Crypto"})).params)
        assert rule_params & filter_params == set()


class TestDescribeTradesFilter:
    def test_empty_is_none(self):
        assert describe_trades_filter(frozenset()) == "none"

    def test_one_category(self):
        assert describe_trades_filter(frozenset({"Crypto"})) == "excluding Crypto"

    def test_two_categories_sorted(self):
        assert (
            describe_trades_filter(frozenset({"Sports", "Crypto"}))
            == "excluding Crypto, Sports"
        )


class TestMarketJoin:
    """Slice 265, Task 1.1: the candle phase's join, as text. The literal
    below is the join as it rendered before ``selection.py`` was split out
    of ``candle_selection.py``; equality after the split is the proof that
    the rename changed no SQL (265 Criterion 5, last clause)."""

    def test_market_join_is_composed_from_the_catalog_join(self):
        """Task 1.5: the candle join extends the shared join, never re-spells it."""
        assert MARKET_JOIN.as_string(None).startswith(CATALOG_JOIN.as_string(None))

    def test_market_join_renders_as_before_the_split(self):
        assert MARKET_JOIN.as_string(None) == (
            "FROM kalshi.markets m "
            "JOIN kalshi.events e ON e.event_ticker = m.event_ticker "
            "JOIN kalshi.series s ON s.ticker = e.series_ticker "
            "LEFT JOIN kalshi.market_candle_state st "
            "ON st.market_ticker = m.ticker AND st.period = %(period)s "
        )
