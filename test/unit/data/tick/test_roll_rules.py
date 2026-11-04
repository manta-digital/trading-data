"""TD1's grammar: round trip, canonical form, and each named refusal (229 FR1)."""

from __future__ import annotations

from typing import Any

import pytest

from manta_trading.data.tick.constants import RollMethod
from manta_trading.data.tick.roll_rules import (
    DEFAULT_ROLL_RULE,
    STATUS_ROLL_RULES,
    RollRule,
)

VALID = [
    RollRule(method, rank, offset)
    for method in RollMethod
    for rank in range(4)
    for offset in range(11)
    if offset == 0 or method is RollMethod.CALENDAR
]


@pytest.mark.parametrize("rule", VALID, ids=str)
def test_parse_inverts_str(rule: RollRule) -> None:
    assert RollRule.parse(str(rule)) == rule


@pytest.mark.parametrize(
    ("text", "canonical"),
    [(" V.0 ", "v.0"), ("c.0+5S", "c.0+5s"), ("C.2", "c.2"), ("\tc.1+1s\n", "c.1+1s")],
)
def test_parse_is_lenient_on_case_and_whitespace(text: str, canonical: str) -> None:
    assert str(RollRule.parse(text)) == canonical


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("z.0", "unknown roll method 'z'; known: c, v, n"),
        ("n.0+2s", "an offset suffix is allowed only on calendar (c)"),
        ("v.-1", "rank must be >= 0, got -1"),
        ("v.x", "rank must be a non-negative integer"),
        ("v.", "rank must be a non-negative integer"),
        ("v.0+2s", "an offset suffix is allowed only on calendar (c)"),
        ("c.0+0s", "offset +0s is written with no suffix"),
        ("c.0+5s junk", "trailing text ' junk'"),
        ("c.0+5", "trailing text '+5'"),
        ("v.1.5", "trailing text '.5'"),
        ("v0", "expected {method}.{rank}[+{offset}s]"),
    ],
)
def test_parse_refuses_with_a_named_message(text: str, message: str) -> None:
    with pytest.raises(ValueError) as caught:
        RollRule.parse(text)
    assert message in str(caught.value)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"method": RollMethod.VOLUME, "rank": -1}, "rank"),
        ({"method": RollMethod.CALENDAR, "offset_sessions": -1}, "offset_sessions"),
        ({"method": RollMethod.VOLUME, "offset_sessions": 2}, "offset_sessions"),
    ],
)
def test_construction_names_the_invalid_field(
    kwargs: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        RollRule(**kwargs)


def test_symbol_prefixes_the_product() -> None:
    assert RollRule(RollMethod.VOLUME).symbol("ES") == "ES.v.0"
    assert RollRule(RollMethod.CALENDAR, 0, 5).symbol("ES") == "ES.c.0+5s"


def test_default_and_status_rules() -> None:
    assert str(DEFAULT_ROLL_RULE) == "v.0"
    assert [str(r) for r in STATUS_ROLL_RULES] == ["v.0", "c.0"]


@pytest.mark.parametrize(("text", "rank"), [("n.0", 0), ("n.1", 1), (" N.2 ", 2)])
def test_open_interest_rules_parse(text: str, rank: int) -> None:
    rule = RollRule.parse(text)
    assert (rule.method, rule.rank, rule.offset_sessions) == (
        RollMethod.OPEN_INTEREST,
        rank,
        0,
    )
    assert str(rule) == f"n.{rank}"
    assert rule.symbol("ES") == f"ES.n.{rank}"


def test_open_interest_is_not_the_default_nor_a_status_rule() -> None:
    assert DEFAULT_ROLL_RULE == RollRule(RollMethod.VOLUME, 0)
    assert all(r.method is not RollMethod.OPEN_INTEREST for r in STATUS_ROLL_RULES)
    assert [str(r) for r in STATUS_ROLL_RULES] == ["v.0", "c.0"]
