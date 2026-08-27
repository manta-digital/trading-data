"""``Settings`` → ``CandleRule`` parsing (slice 264, Tasks 1.2 and 1.3).

Every ``MT_KALSHI_CANDLE_*`` form a ``.env`` author writes is exercised
through monkeypatched environment variables. Nothing here spells the rule's
SQL — that lives only in ``CandleRepository.selection_sql``.
"""

from __future__ import annotations

from typing import Any

import pytest

from manta_trading.config import Settings
from manta_trading.data.kalshi.candle_types import CandleRule

#: The PM's rule C (design 264, Decision 2) — what an empty environment yields.
RULE_C = CandleRule(
    traded_only=True,
    categories=frozenset(),
    excluded_categories=frozenset({"Sports", "Mentions"}),
    excluded_series_pattern=r"MENTION|SAY",
    excluded_title_pattern=r"\m(say|says|mention|mentions)\M",
)

RULE_ENV = (
    "MT_KALSHI_CANDLE_TRADED_ONLY",
    "MT_KALSHI_CANDLE_CATEGORIES",
    "MT_KALSHI_CANDLE_EXCLUDED_CATEGORIES",
    "MT_KALSHI_CANDLE_EXCLUDED_SERIES_PATTERN",
    "MT_KALSHI_CANDLE_EXCLUDED_TITLE_PATTERN",
)


@pytest.fixture(autouse=True)
def _clean_rule_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in RULE_ENV:
        monkeypatch.delenv(name, raising=False)


def load(**overrides: Any) -> Settings:
    """``Settings`` from the (monkeypatched) environment only — never ``.env``."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]


class TestDefaults:
    def test_no_environment_is_rule_c(self):
        assert load().candle_rule() == RULE_C

    def test_rule_is_frozen(self):
        rule = load().candle_rule()
        with pytest.raises(AttributeError):
            rule.traded_only = False  # type: ignore[misc]


class TestCategoryLists:
    def test_comma_list_with_whitespace(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_CANDLE_CATEGORIES", "Sports, Politics")
        assert load().kalshi_candle_categories == frozenset({"Sports", "Politics"})

    def test_empty_value_is_empty_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_CANDLE_CATEGORIES", "")
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_CATEGORIES", "")
        settings = load()
        assert settings.kalshi_candle_categories == frozenset()
        assert settings.kalshi_candle_excluded_categories == frozenset()

    def test_stray_commas_and_blanks_dropped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            "MT_KALSHI_CANDLE_EXCLUDED_CATEGORIES", " Sports,, Crypto , "
        )
        assert load().kalshi_candle_excluded_categories == frozenset(
            {"Sports", "Crypto"}
        )

    def test_programmatic_collection_passes_through(self):
        settings = load(kalshi_candle_categories={"Economics"})
        assert settings.kalshi_candle_categories == frozenset({"Economics"})


class TestBoolAndPatterns:
    @pytest.mark.parametrize(("raw", "expected"), [("false", False), ("true", True)])
    def test_traded_only_parses_bool(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
    ):
        monkeypatch.setenv("MT_KALSHI_CANDLE_TRADED_ONLY", raw)
        assert load().kalshi_candle_traded_only is expected

    def test_empty_pattern_disables_clause(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_SERIES_PATTERN", "")
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_TITLE_PATTERN", "  ")
        rule = load().candle_rule()
        assert rule.excluded_series_pattern is None
        assert rule.excluded_title_pattern is None

    def test_pattern_override_kept_verbatim(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_SERIES_PATTERN", "^KXBTC")
        assert load().candle_rule().excluded_series_pattern == "^KXBTC"


class TestCandleRuleAssembly:
    def test_full_override_builds_expected_rule(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_CANDLE_TRADED_ONLY", "false")
        monkeypatch.setenv("MT_KALSHI_CANDLE_CATEGORIES", "Politics, Economics")
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_CATEGORIES", "")
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_SERIES_PATTERN", "")
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_TITLE_PATTERN", "mention")
        assert load().candle_rule() == CandleRule(
            traded_only=False,
            categories=frozenset({"Politics", "Economics"}),
            excluded_categories=frozenset(),
            excluded_series_pattern=None,
            excluded_title_pattern="mention",
        )


class TestDescribe:
    def test_default_form(self):
        assert (
            RULE_C.describe()
            == "traded 24h · categories all · excluding Mentions, Sports · patterns 2"
        )

    def test_stable_across_calls(self):
        rule = load().candle_rule()
        assert rule.describe() == rule.describe()
        assert rule.describe() == RULE_C.describe()

    def test_reflects_a_changed_rule(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_CANDLE_TRADED_ONLY", "false")
        monkeypatch.setenv("MT_KALSHI_CANDLE_CATEGORIES", "Politics")
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_CATEGORIES", "")
        monkeypatch.setenv("MT_KALSHI_CANDLE_EXCLUDED_SERIES_PATTERN", "")
        assert (
            load().candle_rule().describe()
            == "traded any · categories Politics · excluding none · patterns 1"
        )

    def test_category_sets_render_sorted(self):
        rule = CandleRule(
            traded_only=True,
            categories=frozenset({"Politics", "Crypto", "Economics"}),
            excluded_categories=frozenset(),
            excluded_series_pattern=None,
            excluded_title_pattern=None,
        )
        assert "categories Crypto, Economics, Politics" in rule.describe()
