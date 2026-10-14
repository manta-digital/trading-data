"""Our rules agree with Databento's continuous symbols on recorded real inputs.

LLD 229 TD8, extended per product by LLD 232 TD8. Pins
``VOLUME_RANK_LAG_SESSIONS``: for every product with recorded fixtures and for
``c.0``, ``c.1``, ``v.0`` and ``v.1``, every session resolved over the recorded
span is the contract Databento maps on that session date, and the unresolved
sessions are exactly those whose lag session is not complete. A failure after a
re-record means the provider's convention moved: revisit the lag whole, never
per roll or per product.
"""

from __future__ import annotations

from datetime import date
from functools import cache

import pytest
from tick_support.roll_fixture import (
    provider_intervals,
    provider_span,
    recorded_inputs,
)

from manta_trading.data.tick.constants import VOLUME_RANK_LAG_SESSIONS, RollMethod
from manta_trading.data.tick.roll_resolve import resolve_sessions
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import ActiveContract, Answer, Unresolved
from manta_trading.data.tick.tick_status import TickSessionStatus

#: Products with recorded fixtures under ``test/fixtures/tick/roll/``.
PRODUCTS = ["ES"]
RULES = ["c.0", "c.1", "v.0", "v.1"]


@cache
def _answers(product: str, rule: str) -> tuple[Answer, ...]:
    start, end = provider_span(product)
    return resolve_sessions(RollRule.parse(rule), recorded_inputs(product), start, end)


def _provider(product: str, rule: str, day: date) -> int:
    intervals = provider_intervals(product)[f"{product}.{rule}"]
    (hit,) = [i for i in intervals if i.start <= day < i.end]
    return hit.instrument_id


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("product", PRODUCTS)
def test_every_resolved_session_matches_the_provider(product: str, rule: str) -> None:
    resolved = [a for a in _answers(product, rule) if isinstance(a, ActiveContract)]
    assert len(resolved) > 40
    mismatched = [
        (a.session_date, a.raw_symbol)
        for a in resolved
        if a.instrument_id != _provider(product, rule, a.session_date)
    ]
    assert mismatched == []


def _lag_incomplete(product: str) -> set[date]:
    inputs = recorded_inputs(product)
    start, end = provider_span(product)
    days = [s.session_date for s in inputs.sessions]
    return {
        day
        for i, day in enumerate(days)
        if start <= day < end
        and inputs.statuses[days[i - VOLUME_RANK_LAG_SESSIONS]]
        is not TickSessionStatus.COMPLETE
    }


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("product", PRODUCTS)
def test_unresolved_sessions_are_exactly_those_with_an_incomplete_lag(
    product: str, rule: str
) -> None:
    answers = _answers(product, rule)
    unresolved = {a.session_date for a in answers if isinstance(a, Unresolved)}
    method = RollRule.parse(rule).method
    expected = _lag_incomplete(product) if method is RollMethod.VOLUME else set()
    assert unresolved == expected


def test_tbbo_range_opens_with_two_unresolved_volume_sessions() -> None:
    gap_end = {a.session_date: a for a in _answers("ES", "v.0")}
    assert isinstance(gap_end[date(2024, 11, 4)], Unresolved)
    assert isinstance(gap_end[date(2024, 11, 5)], Unresolved)
    assert isinstance(gap_end[date(2024, 11, 6)], ActiveContract)


def _symbol_on(product: str, rule: str, day: date) -> str:
    (answer,) = [a for a in _answers(product, rule) if a.session_date == day]
    assert isinstance(answer, ActiveContract), answer
    return answer.raw_symbol


@pytest.mark.parametrize(
    ("rule", "day", "symbol"),
    [
        # FR2: calendar
        ("c.0", date(2024, 9, 20), "ESU4"),
        ("c.0", date(2024, 9, 23), "ESZ4"),
        ("c.0", date(2024, 12, 20), "ESZ4"),
        ("c.0", date(2024, 12, 23), "ESH5"),
        # FR3: volume
        ("v.0", date(2024, 9, 17), "ESU4"),
        ("v.0", date(2024, 9, 18), "ESZ4"),
        ("v.0", date(2024, 12, 17), "ESZ4"),
        ("v.0", date(2024, 12, 18), "ESH5"),
        # walkthrough step 4: rank 1 by volume across the December roll
        ("v.1", date(2024, 12, 17), "ESH5"),
        ("v.1", date(2024, 12, 18), "ESZ4"),
        ("v.1", date(2024, 12, 20), "ESZ4"),
        ("v.1", date(2024, 12, 23), "ESM5"),
    ],
)
def test_named_roll_dates(rule: str, day: date, symbol: str) -> None:
    assert _symbol_on("ES", rule, day) == symbol
