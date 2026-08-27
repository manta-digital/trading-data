"""``FakeCandleSource`` — an in-memory ``CandleSource`` for candle-core tests.

Serves scripted candles per ticker for ``GET /markets/candlesticks`` and the
recorded ``historical_cutoff`` fixture, recording every query it receives —
the recorded queries are what proves, at the unit level, which markets the
core asked for and over which windows (Criterion 2). Mirrors the live
endpoint's shapes (Discovery Findings): a known ticker with nothing in the
window is present with an empty list; a ticker in ``omit`` is silently
absent, as an unknown ticker is live. Candles are served when their
``end_period_ts`` lies in ``[start_ts, end_ts]``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from kalshi_support.fake_source import load_fixture
from kalshi_support.samples import CANDLE_SAMPLE
from manta_trading.data.kalshi.constants import CandlePeriod
from manta_trading.data.kalshi.models import (
    Candlestick,
    HistoricalCutoff,
    MarketCandlesticks,
)


def make_candle(end: datetime, **overrides: object) -> Candlestick:
    """A live-shaped candle ending at ``end`` (``CANDLE_SAMPLE`` with overrides)."""
    return Candlestick.model_validate(
        {**CANDLE_SAMPLE, "end_period_ts": int(end.timestamp()), **overrides}
    )


def make_trade_candle(end: datetime, volume: str = "3.00") -> Candlestick:
    return make_candle(end, volume_fp=volume, price={"close_dollars": "0.1100"})


class FakeCandleSource:
    """See the module docstring."""

    def __init__(self) -> None:
        self.candles: dict[str, list[Candlestick]] = {}
        #: Requested tickers the batch endpoint silently omits.
        self.omit: set[str] = set()
        self.cutoff = HistoricalCutoff.model_validate(load_fixture("historical_cutoff"))
        #: Every batch query: ``{"tickers", "start_ts", "end_ts", "period"}``.
        self.candle_queries: list[dict[str, object]] = []
        self.calls: list[str] = []
        self._failures: list[
            tuple[
                str,
                BaseException,
                int | None,
                Callable[[dict[str, object]], bool] | None,
            ]
        ] = []
        self._counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Test-side setup
    # ------------------------------------------------------------------

    def add_candles(self, ticker: str, *rows: Candlestick) -> None:
        self.candles.setdefault(ticker, []).extend(rows)

    def raise_on(
        self,
        call: str,
        exc: BaseException,
        *,
        at: int | None = None,
        when: Callable[[dict[str, object]], bool] | None = None,
    ) -> None:
        """Raise ``exc`` on the ``at``-th invocation of ``call`` and/or when
        ``when(query)`` is true (``call`` is the method name)."""
        self._failures.append((call, exc, at, when))

    def _record(self, call: str, query: dict[str, object]) -> None:
        self.calls.append(call)
        self._counts[call] = self._counts.get(call, 0) + 1
        for name, exc, at, when in self._failures:
            if name != call:
                continue
            if at is not None and self._counts[call] != at:
                continue
            if when is not None and not when(query):
                continue
            raise exc

    # ------------------------------------------------------------------
    # CandleSource
    # ------------------------------------------------------------------

    async def get_markets_candlesticks(
        self,
        tickers: Sequence[str],
        *,
        start_ts: int,
        end_ts: int,
        period_interval: CandlePeriod,
    ) -> list[MarketCandlesticks]:
        query: dict[str, object] = {
            "tickers": tuple(tickers),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period": int(period_interval),
        }
        self.candle_queries.append(query)
        self._record("get_markets_candlesticks", query)
        start = datetime.fromtimestamp(start_ts, tz=UTC)
        end = datetime.fromtimestamp(end_ts, tz=UTC)
        served: list[MarketCandlesticks] = []
        for ticker in tickers:
            if ticker in self.omit:
                continue
            rows = [
                c
                for c in self.candles.get(ticker, [])
                if start <= c.end_period_ts <= end
            ]
            served.append(MarketCandlesticks(market_ticker=ticker, candlesticks=rows))
        return served

    async def get_historical_cutoff(self) -> HistoricalCutoff:
        self._record("get_historical_cutoff", {})
        return self.cutoff
