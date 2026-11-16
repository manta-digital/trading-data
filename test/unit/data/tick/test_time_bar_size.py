"""Slice 309: the time-bar size grammar and the provider-width rule."""

from __future__ import annotations

import pytest

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.time_bar_size import (
    BAR_SIZE_FORM,
    NS_PER_SECOND,
    parse_bar_size,
    provider_schema_for,
)

_WIDTHS = {
    "1s": 1,
    "15s": 15,
    "1m": 60,
    "5m": 300,
    "90m": 5_400,
    "4h": 14_400,
    "24h": 86_400,
}


@pytest.mark.parametrize("token", [*_WIDTHS, "1d"])
def test_accepted_forms(token: str) -> None:
    size = parse_bar_size(token)
    assert size.token == token
    if token == "1d":
        assert size.session_day and size.width_ns is None
    else:
        assert not size.session_day
        assert size.width_ns == _WIDTHS[token] * NS_PER_SECOND


@pytest.mark.parametrize("token", ["1m", "5m", "15m", "1h", "4h", "1d"])
def test_previously_served_sizes_keep_their_spelling(token: str) -> None:
    assert parse_bar_size(token).token == token


def test_case_and_space_normalize() -> None:
    assert parse_bar_size(" 5M ").token == "5m"


@pytest.mark.parametrize(
    "token", ["0s", "-1m", "1.5h", "5x", "", "25h", "2d", "1441m", "86401s"]
)
def test_refusals_name_the_accepted_form(token: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_bar_size(token)
    assert BAR_SIZE_FORM in str(caught.value)


@pytest.mark.parametrize("token", ["1m", "5m", "90m", "4h", "1d", "120s"])
def test_minute_multiples_come_from_provider_minutes(token: str) -> None:
    assert provider_schema_for(parse_bar_size(token)) is TickSchema.OHLCV_1M


@pytest.mark.parametrize("token", ["1s", "15s", "90s"])
def test_sub_minute_sizes_have_no_provider(token: str) -> None:
    assert provider_schema_for(parse_bar_size(token)) is None
