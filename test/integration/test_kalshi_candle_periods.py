"""Integration tests: coarse candles derived from stored minutes (issue 61).

Minute candles are written through the real repository on a throwaway database
(``kalshi_db`` only), then read back through ``serve_candle_periods``. The
values are hand-built so every expected aggregate is computed by eye.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from kalshi_helpers import write_catalog
from test_kalshi_candles import RULE_C, fixture_markets

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi import serve_candle_periods as cp
from manta_trading.data.kalshi.candle_repository import (
    CANDLE_COLUMNS,
    CandleRepository,
)
from manta_trading.data.kalshi.constants import COLLECTED_CANDLE_PERIOD, CandlePeriod

if TYPE_CHECKING:
    from collections.abc import Iterator

    from manta_trading.data.kalshi.repository import CatalogRepository

TICKER = "POLITICS"
STORED = int(COLLECTED_CANDLE_PERIOD)
HOUR = int(CandlePeriod.HOUR)
DAY = int(CandlePeriod.DAY)
D = Decimal
SEP1 = datetime(2026, 9, 1, tzinfo=UTC)
SEP2 = SEP1 + timedelta(days=1)
COLUMN_INDEX = {name: i for i, (name, _) in enumerate(CANDLE_COLUMNS)}


def _side(open_: str, high: str, low: str, close: str) -> km.PriceOhlc:
    return km.PriceOhlc(
        open_dollars=D(open_), high_dollars=D(high), low_dollars=D(low),
        close_dollars=D(close),
    )  # fmt: skip


def _minute(
    end: datetime,
    *,
    bid: tuple[str, str, str, str],
    ask: tuple[str, str, str, str],
    price: km.PriceOhlc,
    volume: str,
    open_interest: str,
) -> km.Candlestick:
    return km.Candlestick(
        end_period_ts=end,
        yes_bid=_side(*bid),
        yes_ask=_side(*ask),
        price=price,
        volume_fp=D(volume),
        open_interest_fp=D(open_interest),
    )


def _no_trade(end: datetime, *, previous: str, bid_close: str, oi: str):
    return _minute(
        end,
        bid=("0.10", "0.14", "0.09", bid_close),
        ask=("0.20", "0.22", "0.19", "0.21"),
        price=km.PriceOhlc(previous_dollars=D(previous)),
        volume="0",
        open_interest=oi,
    )


def _traded(end: datetime, *, ohlc: tuple[str, str, str, str], mean: str, vol: str):
    open_, high, low, close = ohlc
    return _minute(
        end,
        bid=("0.30", "0.31", "0.29", "0.30"),
        ask=("0.40", "0.41", "0.39", "0.40"),
        price=km.PriceOhlc(
            open_dollars=D(open_),
            high_dollars=D(high),
            low_dollars=D(low),
            close_dollars=D(close),
            previous_dollars=D(open_),
            mean_dollars=D(mean),
        ),  # fmt: skip
        volume=vol,
        open_interest="7",
    )


def _at(hour: int, minute: int, *, day: datetime = SEP1) -> datetime:
    return day + timedelta(hours=hour, minutes=minute)


#: Hour ending 11:00 holds four minutes: no trade, trade, trade, no trade.
#: Weighted mean = (2 * 0.50 + 6 * 0.60) / 8 = 0.575.
HOUR_11 = [
    _no_trade(_at(10, 1), previous="0.40", bid_close="0.11", oi="5"),
    _traded(_at(10, 2), ohlc=("0.50", "0.55", "0.45", "0.52"), mean="0.50", vol="2"),
    _traded(_at(10, 3), ohlc=("0.60", "0.65", "0.58", "0.62"), mean="0.60", vol="6"),
    _no_trade(_at(10, 4), previous="0.62", bid_close="0.13", oi="9"),
]
#: Day edges: the minute ending 00:00 on Sep 1 opened Aug 31 23:59 (not Sep 1);
#: the one ending 00:00 on Sep 2 opened Sep 1 23:59 (Sep 1's last).
EDGES = [
    _no_trade(SEP1, previous="0.01", bid_close="0.01", oi="1"),
    _no_trade(_at(0, 1), previous="0.02", bid_close="0.02", oi="2"),
    _no_trade(SEP2, previous="0.03", bid_close="0.03", oi="3"),
]


@pytest.fixture()
def conn(kalshi_db: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(kalshi_db) as connection:
        yield connection


@pytest.fixture()
async def minutes(
    kalshi_repo: CatalogRepository,
    kalshi_conn: psycopg.AsyncConnection[Any],
    conn: psycopg.Connection,
) -> psycopg.Connection:
    markets, series = fixture_markets()
    await write_catalog(kalshi_repo, markets, series)
    repo = CandleRepository(kalshi_conn, RULE_C)
    async with repo.transaction():
        await repo.insert_candles(
            COLLECTED_CANDLE_PERIOD, [(TICKER, c) for c in [*EDGES, *HOUR_11]]
        )
    return conn


def _fetch(conn: psycopg.Connection, period: int, start=None, end=None):
    return cp.fetch_period_candles(
        conn, TICKER, stored_period=STORED, period=period, start=start, end=end
    )


def _value(row, column: str):
    return row.values[COLUMN_INDEX[column]]


class TestHourAggregation:
    async def test_values_follow_the_derivation_rules(self, minutes):
        row = next(r for r in _fetch(minutes, HOUR) if r.end_period_ts == _at(11, 0))
        expected = {
            "yes_bid_open_dollars": D("0.10"),  # first minute's
            "yes_bid_close_dollars": D("0.13"),  # last minute's
            "yes_bid_high_dollars": D("0.31"),
            "yes_bid_low_dollars": D("0.09"),
            "yes_ask_high_dollars": D("0.41"),
            "price_open_dollars": D("0.50"),  # first *traded* minute
            "price_close_dollars": D("0.62"),  # last traded minute
            "price_high_dollars": D("0.65"),
            "price_low_dollars": D("0.45"),
            "price_previous_dollars": D("0.40"),  # first minute's previous
            "price_mean_dollars": D("0.575"),
            "volume_fp": D("8"),
            "open_interest_fp": D("9"),  # last minute's
        }
        for column, value in expected.items():
            assert _value(row, column) == value, column

    async def test_book_movement_without_trades_keeps_previous_and_nulls_ohlc(
        self, minutes
    ):
        """Hour ending 00:01's single no-trade minute: traded OHLC null,
        previous and book filled, as for 1m."""
        row = next(r for r in _fetch(minutes, HOUR) if r.end_period_ts == _at(1, 0))
        assert _value(row, "price_open_dollars") is None
        assert _value(row, "price_close_dollars") is None
        assert _value(row, "price_mean_dollars") is None
        assert _value(row, "price_previous_dollars") == D("0.02")
        assert _value(row, "yes_bid_close_dollars") == D("0.02")

    async def test_periods_with_no_stored_minute_have_no_row(self, minutes):
        ends = [r.end_period_ts for r in _fetch(minutes, HOUR)]
        assert ends == [SEP1, _at(1, 0), _at(11, 0), SEP2]

    async def test_the_in_progress_period_is_served_from_what_is_stored(self, minutes):
        """Hour 11:00 has four of its sixty minutes; it is served, unflagged."""
        assert _at(11, 0) in [r.end_period_ts for r in _fetch(minutes, HOUR)]


class TestDayAggregation:
    async def test_days_end_at_utc_midnight(self, minutes):
        ends = [r.end_period_ts for r in _fetch(minutes, DAY)]
        assert ends == [SEP1, SEP2]

    async def test_the_last_minute_of_a_day_is_in_that_day(self, minutes):
        day = next(r for r in _fetch(minutes, DAY) if r.end_period_ts == SEP2)
        assert _value(day, "open_interest_fp") == D("3")  # minute ending Sep 2 00:00

    async def test_day_is_the_first_minute_to_the_last(self, minutes):
        day = next(r for r in _fetch(minutes, DAY) if r.end_period_ts == SEP2)
        assert _value(day, "price_previous_dollars") == D("0.02")  # 00:01's
        assert _value(day, "price_open_dollars") == D("0.50")
        assert _value(day, "price_close_dollars") == D("0.62")
        assert _value(day, "volume_fp") == D("8")


class TestWindows:
    async def test_a_bare_date_day_window_includes_its_last_candle(self, minutes):
        """``end=D`` is ``end < D+1 00:00``: the day candle ending D+1 00:00
        and the 23:00 hour candle are both in."""
        day = _fetch(minutes, DAY, start=SEP1, end=SEP2)
        assert [r.end_period_ts for r in day] == [SEP2]
        hours = _fetch(minutes, HOUR, start=SEP1, end=SEP2)
        assert hours[-1].end_period_ts == SEP2

    async def test_start_excludes_the_previous_periods_last_candle(self, minutes):
        hours = _fetch(minutes, HOUR, start=SEP1, end=SEP2)
        assert SEP1 not in [r.end_period_ts for r in hours]

    async def test_a_window_inside_one_period_still_returns_that_period(self, minutes):
        """Opens in [start, end): the hour opening 10:00 is in a 10:30-11:00
        window? No — it opened before start."""
        assert _fetch(minutes, HOUR, start=_at(10, 30), end=_at(11, 0)) == []
        rows = _fetch(minutes, HOUR, start=_at(10, 0), end=_at(10, 30))
        assert [r.end_period_ts for r in rows] == [_at(11, 0)]

    @pytest.mark.parametrize("period", [HOUR, DAY])
    @pytest.mark.parametrize(
        ("start", "end"),
        [(None, None), (SEP1, SEP2), (_at(10, 0), None), (None, _at(1, 0))],
    )
    async def test_count_equals_len_fetch(self, minutes, period, start, end):
        count = cp.count_period_candles(
            minutes, TICKER, stored_period=STORED, period=period, start=start, end=end
        )
        assert count == len(_fetch(minutes, period, start, end))

    async def test_count_is_served_rows_not_minutes_read(self, minutes):
        count = cp.count_period_candles(
            minutes, TICKER, stored_period=STORED, period=DAY, start=None, end=None
        )
        assert count == 2  # seven minute rows, two day candles
