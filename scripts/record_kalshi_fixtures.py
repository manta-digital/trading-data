"""Record real Kalshi API responses as unit-test fixtures (slice 261).

Drives ``KalshiClient`` (public mode) against the live API through a
recording transport that captures the **raw wire body** of every response —
what is written is exactly what Kalshi served, never a model
re-serialization. Files land in ``test/fixtures/kalshi/<name>.json``.

Manual, developer-run only. It performs live external requests through the
client's own rate limiter (so it honours the public budget) and must NEVER
run in CI — tests consume only the committed fixtures. Rerun it only to
refresh them.

Usage::

    uv run python scripts/record_kalshi_fixtures.py                 # record all
    uv run python scripts/record_kalshi_fixtures.py --only trades   # one recorder
    uv run python scripts/record_kalshi_fixtures.py --only historical_cutoff --dry-run

``--dry-run`` prints each response (truncated) instead of writing files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    CANDLE_BATCH_MAX_TICKERS,
    KALSHI_MVE_FILTER,
    CandlePeriod,
    MarketStatusFilter,
)
from manta_trading.data.kalshi.models import Market
from manta_trading.providers.errors import ProviderPermanentError

DESCRIPTION = "Record real Kalshi API responses as unit-test fixtures."
FIXTURE_DIR = Path("test/fixtures/kalshi")
PAGE_LIMIT = 5
#: Small category keeps the (unpaginated) series-list fixture a sane size.
SERIES_CATEGORY = "Health"
SERIES_TICKER = "FED"
UNKNOWN_TICKER = "NOPE-NOT-A-TICKER"
CANDLE_WINDOW = timedelta(hours=24)
#: Slice 262: ``tickers`` batch lookups and one settled window.
TICKERS_BATCH_SAMPLE = 5
SETTLED_WINDOW_SPAN = timedelta(hours=1)
SETTLED_WINDOW_LIMIT = 50
CANDLE_TRADE_SCAN = 100
#: Up to this span, record 1-minute candles; beyond it, hourly.
MINUTE_CANDLE_MAX_SPAN = timedelta(hours=6)
#: Slice 264: the batch endpoint over the last hour of 1-minute candles for a
#: few traded tickers plus never-traded open markets. Observed live 2026-08-26:
#: a quote-only candle on a market that has *ever* traded carries
#: ``price: {"previous_dollars": …}``; only a never-traded market serves
#: ``price: {}``. Never-traded markets on the first open page are quoted
#: every hour; the idle ones (an entry with an empty candle list) are the
#: long-dated ones, so half the sample closes at least ``FAR_CLOSE`` out.
CANDLE_BATCH_WINDOW = timedelta(hours=1)
CANDLE_BATCH_TRADED_SAMPLE = 3
CANDLE_BATCH_NEVER_TRADED_SAMPLE = 5
CANDLE_BATCH_OPEN_SCAN = 100
CANDLE_BATCH_FAR_CLOSE = timedelta(days=180)
#: Slice 264: 100 tickers × 360 minutes = 36,000 requested candles, above the
#: verified 10,000 cap — provokes the HTTP 400 the planner exists to avoid.
CANDLE_OVER_CAP_WINDOW = timedelta(minutes=360)
#: Slice 265: one windowed page of the public tape and that window's last
#: page, plus a window with nothing in it. One minute of the tape holds a few
#: thousand trades (measured 2026-08-27: 300–550 k/hour), so ``limit=100``
#: guarantees a cursor on the first page; a window a year out is empty.
TRADES_WINDOW_SPAN = timedelta(minutes=1)
TRADES_WINDOW_LIMIT = 100
TRADES_EMPTY_WINDOW_OFFSET = timedelta(days=365)
DRY_RUN_PREVIEW_CHARS = 600


class RecordingTransport(httpx.AsyncBaseTransport):
    """Wraps the real transport and keeps the last raw response body."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.last_body: bytes = b""
        self.last_status: int = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        self.last_body = await response.aread()
        self.last_status = response.status_code
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class Recorder:
    def __init__(
        self, client: KalshiClient, transport: RecordingTransport, dry_run: bool
    ):
        self.client = client
        self.transport = transport
        self.dry_run = dry_run

    def save(self, name: str) -> None:
        """Persist (or preview) the most recent raw response under ``name``."""
        body = self.transport.last_body
        json.loads(body)  # fail loudly on a non-JSON capture
        if self.dry_run:
            preview = body.decode()[:DRY_RUN_PREVIEW_CHARS]
            print(f"--- {name} (HTTP {self.transport.last_status}, {len(body)} bytes)")
            print(preview + ("…" if len(body) > DRY_RUN_PREVIEW_CHARS else ""))
            return
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        path = FIXTURE_DIR / f"{name}.json"
        path.write_bytes(body)
        print(f"wrote {path} ({len(body)} bytes)")


async def record_series_list(rec: Recorder) -> None:
    await rec.client.get_series_list(category=SERIES_CATEGORY)
    rec.save("series_list")


async def record_series(rec: Recorder) -> None:
    await rec.client.get_series(SERIES_TICKER)
    rec.save("series")


async def record_events(rec: Recorder) -> None:
    page = await rec.client.get_events(limit=PAGE_LIMIT)
    rec.save("events_page1")
    await rec.client.get_events(limit=PAGE_LIMIT, cursor=page.cursor)
    rec.save("events_page2")
    await rec.client.get_event(page.events[0].event_ticker, with_nested_markets=True)
    rec.save("event")


async def record_markets(rec: Recorder) -> None:
    page = await rec.client.get_markets(
        limit=PAGE_LIMIT, status=MarketStatusFilter.SETTLED
    )
    rec.save("markets_page1")
    await rec.client.get_markets(
        limit=PAGE_LIMIT, status=MarketStatusFilter.SETTLED, cursor=page.cursor
    )
    rec.save("markets_page2")
    await rec.client.get_markets(limit=PAGE_LIMIT, status=MarketStatusFilter.OPEN)
    rec.save("markets_open")
    await rec.client.get_market(page.markets[0].ticker)
    rec.save("market")


async def record_candlesticks(rec: Recorder) -> None:
    # A market that is trading *right now* (the most-traded ticker on the
    # latest trades page) so the window is guaranteed to contain candles
    # with real OHLC. The series ticker comes from the market's event because
    # market objects do not carry it.
    trades = await rec.client.get_trades(limit=CANDLE_TRADE_SCAN)
    ticker = Counter(t.ticker for t in trades.trades).most_common(1)[0][0]
    market = await rec.client.get_market(ticker)
    event = await rec.client.get_event(market.event_ticker)
    now = datetime.now(UTC)
    start = max(market.open_time or now - CANDLE_WINDOW, now - CANDLE_WINDOW)
    end = min(market.close_time, now)
    period = (
        CandlePeriod.MINUTE
        if end - start <= MINUTE_CANDLE_MAX_SPAN
        else CandlePeriod.HOUR
    )
    await rec.client.get_market_candlesticks(
        event.series_ticker,
        market.ticker,
        start_ts=int(start.timestamp()),
        end_ts=int(end.timestamp()),
        period_interval=period,
    )
    rec.save("candlesticks")


async def record_trades(rec: Recorder) -> None:
    page = await rec.client.get_trades(limit=PAGE_LIMIT)
    rec.save("trades_page1")
    await rec.client.get_trades(limit=PAGE_LIMIT, cursor=page.cursor)
    rec.save("trades_page2")


async def record_trades_window(rec: Recorder) -> None:
    """``trades_window`` and ``trades_window_last`` — the first and the last
    page of the same one-minute window ending at the current minute (the
    bounds are printed so the pair can be re-recorded)."""
    start_ts, end_ts = _window_ending_now(TRADES_WINDOW_SPAN)
    print(
        f"trades window min_ts={start_ts} max_ts={end_ts} limit={TRADES_WINDOW_LIMIT}"
    )
    page = await rec.client.get_trades(
        min_ts=start_ts, max_ts=end_ts, limit=TRADES_WINDOW_LIMIT
    )
    if not page.cursor:
        raise SystemExit(
            f"the last minute served {len(page.trades)} trades, under the page "
            f"limit {TRADES_WINDOW_LIMIT}: no cursor to record; retry in a "
            "busier minute"
        )
    rec.save("trades_window")
    cursor: str | None = page.cursor
    while cursor:
        page = await rec.client.get_trades(
            min_ts=start_ts, max_ts=end_ts, limit=TRADES_WINDOW_LIMIT, cursor=cursor
        )
        cursor = page.cursor
    rec.save("trades_window_last")


async def record_trades_empty(rec: Recorder) -> None:
    """``trades_empty`` — a one-minute window a year ahead: no trades, no cursor."""
    start_ts, end_ts = _window_ending_now(TRADES_WINDOW_SPAN)
    ahead = int(TRADES_EMPTY_WINDOW_OFFSET.total_seconds())
    await rec.client.get_trades(
        min_ts=start_ts + ahead, max_ts=end_ts + ahead, limit=TRADES_WINDOW_LIMIT
    )
    rec.save("trades_empty")


async def record_historical_cutoff(rec: Recorder) -> None:
    await rec.client.get_historical_cutoff()
    rec.save("historical_cutoff")


async def record_error_404(rec: Recorder) -> None:
    try:
        await rec.client.get_market(UNKNOWN_TICKER)
    except ProviderPermanentError:
        # Expected: the 404 body is the fixture; the client's classification
        # of it is exactly what the error-path tests exercise.
        rec.save("error_404")
        return
    raise SystemExit(f"expected a 404 for {UNKNOWN_TICKER!r}; the API returned success")


def _recorded_tickers(fixture: str, key: str, field: str) -> list[str]:
    """Tickers already recorded under ``fixture`` (keeps targets consistent)."""
    payload = json.loads((FIXTURE_DIR / f"{fixture}.json").read_text(encoding="utf-8"))
    return [row[field] for row in payload[key][:TICKERS_BATCH_SAMPLE]]


async def record_markets_by_tickers(rec: Recorder) -> None:
    """``GET /markets?tickers=`` — recorded tickers plus one bogus ticker, to
    prove the API silently omits unknown tickers (slice 262, Decision 9).

    Tickers come from ``markets_settled_window`` (record that first): the
    261 pages are MVE parlays, which ``mve_filter=exclude`` drops from a
    ``tickers`` lookup too — observed live 2026-08-25, an empty response.
    """
    tickers = [
        *_recorded_tickers("markets_settled_window", "markets", "ticker"),
        UNKNOWN_TICKER,
    ]
    await rec.client.get_markets(tickers=",".join(tickers), mve_filter="exclude")
    rec.save("markets_by_tickers")


async def record_events_by_tickers(rec: Recorder) -> None:
    """``GET /events?tickers=`` with recorded event tickers."""
    tickers = _recorded_tickers("events_page1", "events", "event_ticker")
    await rec.client.get_events(tickers=",".join(tickers))
    rec.save("events_by_tickers")


async def record_markets_settled_window(rec: Recorder) -> None:
    """One recent settled window, non-MVE, as the sync core requests it."""
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - SETTLED_WINDOW_SPAN
    await rec.client.get_markets(
        min_settled_ts=int(start.timestamp()),
        max_settled_ts=int(end.timestamp()),
        mve_filter="exclude",
        limit=SETTLED_WINDOW_LIMIT,
    )
    rec.save("markets_settled_window")


def _window_ending_now(span: timedelta) -> tuple[int, int]:
    """``(start_ts, end_ts)`` for the ``span`` ending at the current minute."""
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    return int((end - span).timestamp()), int(end.timestamp())


async def _open_markets(
    rec: Recorder, count: int, *, min_close_ts: int | None = None
) -> list[Market]:
    page = await rec.client.get_markets(
        status=MarketStatusFilter.OPEN,
        mve_filter=KALSHI_MVE_FILTER,
        min_close_ts=min_close_ts,
        limit=count,
    )
    return page.markets


async def _never_traded_tickers(
    rec: Recorder, *, min_close_ts: int | None = None
) -> list[str]:
    markets = await _open_markets(
        rec, CANDLE_BATCH_OPEN_SCAN, min_close_ts=min_close_ts
    )
    never = [m.ticker for m in markets if not m.volume_fp]
    return never[:CANDLE_BATCH_NEVER_TRADED_SAMPLE]


async def record_candlesticks_batch(rec: Recorder) -> None:
    """``GET /markets/candlesticks`` (slice 264): traded tickers from the
    latest trades page plus never-traded open markets, last hour, ``period=1``.

    The fixture must show both shapes the core handles — an entry with an
    empty ``candlesticks`` list (an idle market) and a candle with
    ``price: {}`` (a quote-only period on a never-traded market) — so the
    response is checked for both before it is saved; the window and the
    sample decide, so do not assume.
    """
    trades = await rec.client.get_trades(limit=CANDLE_TRADE_SCAN)
    traded = [
        ticker
        for ticker, _ in Counter(t.ticker for t in trades.trades).most_common(
            CANDLE_BATCH_TRADED_SAMPLE
        )
    ]
    far_close = int((datetime.now(UTC) + CANDLE_BATCH_FAR_CLOSE).timestamp())
    quoted = await _never_traded_tickers(rec)
    idle = await _never_traded_tickers(rec, min_close_ts=far_close)
    tickers = list(dict.fromkeys([*traded, *quoted, *idle]))
    start_ts, end_ts = _window_ending_now(CANDLE_BATCH_WINDOW)
    await rec.client.get_markets_candlesticks(
        tickers, start_ts=start_ts, end_ts=end_ts, period_interval=CandlePeriod.MINUTE
    )
    served = json.loads(rec.transport.last_body)["markets"]
    has_empty = any(not entry["candlesticks"] for entry in served)
    has_quote_only = any(
        candle["price"] == {} for entry in served for candle in entry["candlesticks"]
    )
    if not (has_empty and has_quote_only):
        raise SystemExit(
            "candlesticks_batch: response lacks an empty entry "
            f"({has_empty}) or a price:{{}} candle ({has_quote_only}); rerun"
        )
    rec.save("candlesticks_batch")


async def record_candlesticks_batch_over_cap(rec: Recorder) -> None:
    """The HTTP 400 body for a request over the batch candle cap (slice 264)."""
    tickers = [m.ticker for m in await _open_markets(rec, CANDLE_BATCH_MAX_TICKERS)]
    if len(tickers) != CANDLE_BATCH_MAX_TICKERS:
        raise SystemExit(
            f"expected {CANDLE_BATCH_MAX_TICKERS} open tickers, got {len(tickers)}"
        )
    start_ts, end_ts = _window_ending_now(CANDLE_OVER_CAP_WINDOW)
    try:
        await rec.client.get_markets_candlesticks(
            tickers,
            start_ts=start_ts,
            end_ts=end_ts,
            period_interval=CandlePeriod.MINUTE,
        )
    except ProviderPermanentError:
        # Expected: the 400 body is the fixture (the cap is on the request).
        rec.save("error_400_candles_cap")
        return
    raise SystemExit("expected HTTP 400 over the candle cap; the API returned success")


RECORDERS: dict[str, Callable[[Recorder], Awaitable[None]]] = {
    "series_list": record_series_list,
    "series": record_series,
    "events": record_events,
    "markets": record_markets,
    "candlesticks": record_candlesticks,
    "trades": record_trades,
    "trades_window": record_trades_window,
    "trades_window_last": record_trades_window,
    "trades_empty": record_trades_empty,
    "historical_cutoff": record_historical_cutoff,
    "error_404": record_error_404,
    "markets_settled_window": record_markets_settled_window,
    "markets_by_tickers": record_markets_by_tickers,
    "events_by_tickers": record_events_by_tickers,
    "candlesticks_batch": record_candlesticks_batch,
    "candlesticks_batch_over_cap": record_candlesticks_batch_over_cap,
}


async def run(names: list[str], dry_run: bool) -> None:
    transport = RecordingTransport()
    client = KalshiClient(transport=transport)
    rec = Recorder(client, transport, dry_run)
    try:
        for name in names:
            await RECORDERS[name](rec)
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--only", choices=sorted(RECORDERS), help="run one recorder")
    parser.add_argument(
        "--dry-run", action="store_true", help="print responses, write nothing"
    )
    args = parser.parse_args(argv)
    names = [args.only] if args.only else list(RECORDERS)
    asyncio.run(run(names, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
