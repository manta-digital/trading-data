"""``Settings`` → ``CollectionRule`` parsing (slice 264, Tasks 1.2 and 1.3;
renamed in slice 265, Task 1.4) and the rename guard.

Every ``MT_KALSHI_COLLECTION_*`` form a ``.env`` author writes is exercised
through monkeypatched environment variables. Nothing here spells the rule's
SQL — that lives only in ``selection.selection_sql``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from manta_trading.config import (
    KALSHI_COLLECTION_ENV_PREFIX,
    RENAMED_KALSHI_CANDLE_ENV_PREFIX,
    RenamedSettingError,
    Settings,
)
from manta_trading.data.kalshi.selection import CollectionRule

#: The PM's rule C (design 264, Decision 2) — what an empty environment yields.
RULE_C = CollectionRule(
    traded_only=True,
    categories=frozenset(),
    excluded_categories=frozenset({"Sports", "Mentions"}),
    excluded_series_pattern=r"MENTION|SAY",
    excluded_title_pattern=r"\m(say|says|mention|mentions)\M",
)

RULE_ENV = (
    "MT_KALSHI_COLLECTION_TRADED_ONLY",
    "MT_KALSHI_COLLECTION_CATEGORIES",
    "MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES",
    "MT_KALSHI_COLLECTION_EXCLUDED_SERIES_PATTERN",
    "MT_KALSHI_COLLECTION_EXCLUDED_TITLE_PATTERN",
)
#: The pre-265 names, derived so a sixth setting cannot be forgotten here.
OLD_RULE_ENV = tuple(
    RENAMED_KALSHI_CANDLE_ENV_PREFIX + name.removeprefix(KALSHI_COLLECTION_ENV_PREFIX)
    for name in RULE_ENV
)


@pytest.fixture(autouse=True)
def _clean_rule_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*RULE_ENV, *OLD_RULE_ENV):
        monkeypatch.delenv(name, raising=False)


def load(**overrides: Any) -> Settings:
    """``Settings`` from the (monkeypatched) environment only — never ``.env``."""
    return Settings(_env_file=None, **overrides)


class TestNaming:
    def test_prefix_constant_fields_and_this_file_agree(self):
        """The prefix is spelled once: the five fields are it under MT_."""
        fields = [
            n for n in Settings.model_fields if n.startswith("kalshi_collection_")
        ]
        assert {f"MT_{name.upper()}" for name in fields} == set(RULE_ENV)
        assert all(
            f"MT_{name.upper()}".startswith(KALSHI_COLLECTION_ENV_PREFIX)
            for name in fields
        )


class TestDefaults:
    def test_no_environment_is_rule_c(self):
        assert load().collection_rule() == RULE_C

    def test_rule_is_frozen(self):
        rule = load().collection_rule()
        with pytest.raises(AttributeError):
            rule.traded_only = False  # type: ignore[misc]


class TestCategoryLists:
    def test_comma_list_with_whitespace(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_COLLECTION_CATEGORIES", "Sports, Politics")
        assert load().kalshi_collection_categories == frozenset({"Sports", "Politics"})

    def test_empty_value_is_empty_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_COLLECTION_CATEGORIES", "")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES", "")
        settings = load()
        assert settings.kalshi_collection_categories == frozenset()
        assert settings.kalshi_collection_excluded_categories == frozenset()

    def test_stray_commas_and_blanks_dropped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            "MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES", " Sports,, Crypto , "
        )
        assert load().kalshi_collection_excluded_categories == frozenset(
            {"Sports", "Crypto"}
        )

    def test_programmatic_collection_passes_through(self):
        settings = load(kalshi_collection_categories={"Economics"})
        assert settings.kalshi_collection_categories == frozenset({"Economics"})


class TestBoolAndPatterns:
    @pytest.mark.parametrize(("raw", "expected"), [("false", False), ("true", True)])
    def test_traded_only_parses_bool(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
    ):
        monkeypatch.setenv("MT_KALSHI_COLLECTION_TRADED_ONLY", raw)
        assert load().kalshi_collection_traded_only is expected

    def test_empty_pattern_disables_clause(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_SERIES_PATTERN", "")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_TITLE_PATTERN", "  ")
        rule = load().collection_rule()
        assert rule.excluded_series_pattern is None
        assert rule.excluded_title_pattern is None

    def test_pattern_override_kept_verbatim(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_SERIES_PATTERN", "^KXBTC")
        assert load().collection_rule().excluded_series_pattern == "^KXBTC"


class TestCollectionRuleAssembly:
    def test_full_override_builds_expected_rule(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_COLLECTION_TRADED_ONLY", "false")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_CATEGORIES", "Politics, Economics")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES", "")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_SERIES_PATTERN", "")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_TITLE_PATTERN", "mention")
        assert load().collection_rule() == CollectionRule(
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
        rule = load().collection_rule()
        assert rule.describe() == rule.describe()
        assert rule.describe() == RULE_C.describe()

    def test_reflects_a_changed_rule(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MT_KALSHI_COLLECTION_TRADED_ONLY", "false")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_CATEGORIES", "Politics")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES", "")
        monkeypatch.setenv("MT_KALSHI_COLLECTION_EXCLUDED_SERIES_PATTERN", "")
        assert (
            load().collection_rule().describe()
            == "traded any · categories Politics · excluding none · patterns 1"
        )

    def test_category_sets_render_sorted(self):
        rule = CollectionRule(
            traded_only=True,
            categories=frozenset({"Politics", "Crypto", "Economics"}),
            excluded_categories=frozenset(),
            excluded_series_pattern=None,
            excluded_title_pattern=None,
        )
        assert "categories Crypto, Economics, Politics" in rule.describe()


class TestRenameGuard:
    """Slice 265, Task 1.4: an ``MT_KALSHI_CANDLE_*`` still set must fail
    loudly, naming the new variable — from either source pydantic-settings
    reads, since ``extra="ignore"`` would otherwise drop it silently."""

    @pytest.mark.parametrize("name", OLD_RULE_ENV)
    def test_loud_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ):
        monkeypatch.setenv(name, "x")
        new_name = KALSHI_COLLECTION_ENV_PREFIX + name.removeprefix(
            RENAMED_KALSHI_CANDLE_ENV_PREFIX
        )
        with pytest.raises(RenamedSettingError, match=re.escape(new_name)):
            load()

    @pytest.mark.parametrize("name", OLD_RULE_ENV)
    def test_loud_from_the_env_file(self, tmp_path: Path, name: str):
        env_file = tmp_path / ".env"
        env_file.write_text(f"{name}=x\n")
        new_name = KALSHI_COLLECTION_ENV_PREFIX + name.removeprefix(
            RENAMED_KALSHI_CANDLE_ENV_PREFIX
        )
        with pytest.raises(RenamedSettingError, match=re.escape(new_name)):
            Settings(_env_file=env_file)

    def test_new_names_read_from_the_env_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("MT_KALSHI_COLLECTION_CATEGORIES=Politics\n")
        rule = Settings(_env_file=env_file).collection_rule()
        assert rule.categories == frozenset({"Politics"})

    def test_missing_env_file_is_not_an_error(self, tmp_path: Path):
        assert Settings(_env_file=tmp_path / "absent").collection_rule() == RULE_C
