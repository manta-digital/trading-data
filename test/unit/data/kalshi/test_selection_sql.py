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

from manta_trading.data.kalshi.candle_selection import (
    Selection,
    SelectionForm,
    selection_sql,
)
from manta_trading.data.kalshi.candle_types import CandleRule

EMPTY = CandleRule(
    traded_only=False,
    categories=frozenset(),
    excluded_categories=frozenset(),
    excluded_series_pattern=None,
    excluded_title_pattern=None,
)
RULE_C = CandleRule(
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
        rule = CandleRule(False, frozenset({"Sports"}), frozenset(), None, None)
        selection = selection_sql(rule, "recent")
        assert list(selection.params) == ["candle_categories"]
        assert selection.params["candle_categories"] == ["Sports"]
        assert "s.category = ANY(%(candle_categories)s)" in text(selection)

    def test_exclude_list_alone(self):
        rule = CandleRule(False, frozenset(), frozenset({"Sports"}), None, None)
        selection = selection_sql(rule, "recent")
        assert list(selection.params) == ["candle_excluded_categories"]

    def test_series_pattern_alone(self):
        rule = CandleRule(False, frozenset(), frozenset(), "MENTION", None)
        selection = selection_sql(rule, "recent")
        assert list(selection.params) == ["candle_excluded_series_pattern"]
        assert selection.params["candle_excluded_series_pattern"] == "MENTION"

    def test_title_pattern_alone(self):
        rule = CandleRule(False, frozenset(), frozenset(), None, "say")
        selection = selection_sql(rule, "recent")
        assert list(selection.params) == ["candle_excluded_title_pattern"]

    @pytest.mark.parametrize(
        ("form", "column"), [("recent", "volume_24h_fp"), ("ever", "volume_fp")]
    )
    def test_traded_alone_binds_no_value_and_picks_the_form_column(
        self, form: SelectionForm, column: str
    ):
        rule = CandleRule(True, frozenset(), frozenset(), None, None)
        selection = selection_sql(rule, form)
        assert selection.params == {}
        identifiers = [
            leaf
            for leaf in _walk(selection.predicate)
            if isinstance(leaf, sql.Identifier)
        ]
        assert len(identifiers) == 1
        assert identifiers[0] == sql.Identifier(column)

    def test_rule_c_default(self):
        selection = selection_sql(RULE_C, "recent")
        assert list(selection.params) == [
            "candle_excluded_categories",
            "candle_excluded_series_pattern",
            "candle_excluded_title_pattern",
        ]
        # Category sets are bound sorted, so equal rules bind equal params.
        assert selection.params["candle_excluded_categories"] == ["Mentions", "Sports"]
        assert text(selection).count(" AND ") == 3


class TestNullAsymmetry:
    """Task 4.1 / review F003: exclusions keep an uncategorised or untitled
    series; the allow-list deliberately does not match one."""

    def test_exclusion_clauses_use_coalesce(self):
        selection = selection_sql(RULE_C, "recent")
        body = text(selection)
        assert "COALESCE(s.category, '') <> ALL(%(candle_excluded_categories)s)" in body
        assert "COALESCE(s.title, '') !~* %(candle_excluded_title_pattern)s" in body
        # ``series.ticker`` is the primary key: no COALESCE needed or used.
        assert "s.ticker !~ %(candle_excluded_series_pattern)s" in body

    def test_allow_list_is_not_wrapped_in_coalesce(self):
        rule = CandleRule(False, frozenset({"Sports"}), frozenset(), None, None)
        body = text(selection_sql(rule, "recent"))
        assert "s.category = ANY(%(candle_categories)s)" in body
        assert "COALESCE(s.category" not in body

    def test_every_value_is_a_bound_parameter(self):
        """The operator's regex must never reach the SQL text."""
        values = ("Zq-cat", "Zq-excl", "Zq-series-re", "Zq-title-re")
        rule = CandleRule(
            True, frozenset({values[0]}), frozenset({values[1]}), values[2], values[3]
        )
        selection = selection_sql(rule, "ever")
        body = text(selection)
        for value in values:
            assert value not in body
        assert set(selection.params) == {
            "candle_categories",
            "candle_excluded_categories",
            "candle_excluded_series_pattern",
            "candle_excluded_title_pattern",
        }
