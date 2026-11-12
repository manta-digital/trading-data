"""``TickBarCutter`` against the independent oracle on real trades (308 D1, D2, D9)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.dbn_values import UNDEF_INT64
from manta_trading.data.tick.tick_bars import (
    TickBar,
    TickBarCutter,
    TickBarInputError,
    Trade,
)

from .tick_bar_oracle import (
    FIRST_SESSION,
    SECOND_SESSION,
    SESSION_COUNTS,
    fixture_trades,
    oracle_bars,
)

TBBO = TickSchema.TBBO
FIRST_COUNT = SESSION_COUNTS[FIRST_SESSION]


def cut(trades: Sequence[Trade], n: int) -> list[TickBar]:
    cutter = TickBarCutter(n)
    bars = [bar for trade in trades for bar in cutter.push(trade)]
    last = cutter.close_session()
    return bars if last is None else [*bars, last]


@pytest.mark.parametrize(
    "n",
    [
        1,
        997,  # prime: leaves a partial bar in both sessions
        FIRST_COUNT,  # one full, non-partial bar for the first session
        FIRST_COUNT + 1,  # one partial bar per session
        7,
    ],
)
def test_cutter_matches_oracle(n: int) -> None:
    trades = fixture_trades(TBBO)
    assert cut(trades, n) == oracle_bars(trades, n)


def test_n_equal_to_a_session_count_is_one_full_bar() -> None:
    first = [
        b
        for b in cut(fixture_trades(TBBO), FIRST_COUNT)
        if b.session_date == FIRST_SESSION
    ]
    assert len(first) == 1 and not first[0].partial
    assert first[0].trade_count == FIRST_COUNT


def test_n_above_every_session_count_is_one_partial_bar_each() -> None:
    bars = cut(fixture_trades(TBBO), 10_000)
    assert [(b.session_date, b.index, b.partial) for b in bars] == [
        (FIRST_SESSION, 0, True),
        (SECOND_SESSION, 0, True),
    ]


@pytest.mark.parametrize("n", [1, 4, 6, 41, 984, 1968])
def test_count_restarts_and_last_bar_is_partial_exactly_off_multiple(n: int) -> None:
    bars = cut(fixture_trades(TBBO), n)
    first = [b for b in bars if b.session_date == FIRST_SESSION]
    second = [b for b in bars if b.session_date == SECOND_SESSION]
    assert [b.index for b in first] == list(range(len(first)))
    assert second[0].index == 0
    assert first[-1].partial is (FIRST_COUNT % n != 0)
    assert all(not b.partial and b.trade_count == n for b in first[:-1])


def test_repeated_ts_sequence_records_are_separate_trades() -> None:
    trades = fixture_trades(TBBO)
    repeated = [t for t in trades if t.sequence_ordinal > 0]
    assert repeated, "the fixture holds repeated (ts_event, sequence) records"
    bars = cut(trades, 1)
    assert len(bars) == len(trades)
    assert sum(b.trade_count for b in cut(trades, 13)) == len(trades)


@pytest.mark.parametrize("n", [1, 5, 100])
def test_side_volumes_add_up(n: int) -> None:
    trades = fixture_trades(TBBO)
    side_n = {t.ts_event: t.size for t in trades if t.side == "N"}
    assert side_n, "the fixture holds a side-N trade"
    for bar in cut(trades, n):
        no_side = sum(
            t.size
            for t in trades
            if t.side == "N"
            and t.session_date == bar.session_date
            and bar.open_time <= t.ts_event <= bar.close_time
        )
        assert bar.buy_volume + bar.sell_volume + no_side == bar.volume


@pytest.mark.parametrize("n", [1, 997, 50])
def test_trades_twin_matches_tbbo_without_quotes(n: int) -> None:
    tbbo = cut(fixture_trades(TBBO), n)
    trades = cut(fixture_trades(TickSchema.TRADES), n)
    assert [replace(b, bid_px=None, ask_px=None) for b in tbbo] == trades
    assert all(b.bid_px is None and b.ask_px is None for b in trades)
    assert any(b.bid_px is not None for b in tbbo)


def test_current_does_not_change_output() -> None:
    trades = fixture_trades(TBBO)
    cutter = TickBarCutter(37)
    bars: list[TickBar] = []
    for i, trade in enumerate(trades):
        bars.extend(cutter.push(trade))
        if i % 11 == 0:
            now = cutter.current()
            assert now is None or now.partial
    last = cutter.close_session()
    assert [*bars, last] == oracle_bars(trades, 37)


def test_close_session_after_a_full_bar_and_twice_is_none() -> None:
    trades = fixture_trades(TBBO)[:10]
    cutter = TickBarCutter(5)
    bars = [bar for trade in trades for bar in cutter.push(trade)]
    assert len(bars) == 2 and cutter.current() is None
    assert cutter.close_session() is None
    cutter = TickBarCutter(3)
    for trade in trades:
        cutter.push(trade)
    assert cutter.close_session() is not None
    assert cutter.close_session() is None


def _trade(ts: int, seq: int = 1, ordinal: int = 0, **kw: Any) -> Trade:
    base = Trade(
        ts, seq, ordinal, date(2024, 12, 3), 6_000_000_000_000, 1, "B", None, None
    )
    return base._replace(**kw)


@pytest.mark.parametrize(
    ("second", "reason"),
    [
        (_trade(100, 5, 2), "equal key"),
        (_trade(100, 5, 1), "lower key"),
        (_trade(99, 9, 9), "lower ts_event"),
        (_trade(101, session_date=date(2024, 12, 2)), "session backwards"),
        (_trade(101, price=UNDEF_INT64), "undefined price"),
    ],
)
def test_bad_input_raises_with_both_keys(second: Trade, reason: str) -> None:
    cutter = TickBarCutter(3)
    first = _trade(100, 5, 2)
    cutter.push(first)
    with pytest.raises(TickBarInputError) as raised:
        cutter.push(second)
    previous = (first.ts_event, first.sequence, first.sequence_ordinal)
    current = (second.ts_event, second.sequence, second.sequence_ordinal)
    assert raised.value.keys == (previous, current), reason
    assert str(previous) in str(raised.value) and str(current) in str(raised.value)


@pytest.mark.parametrize("n", [0, -3])
def test_ticks_per_bar_below_one_is_refused(n: int) -> None:
    with pytest.raises(ValueError, match="ticks_per_bar"):
        TickBarCutter(n)
