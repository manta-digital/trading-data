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
from manta_trading.data.kalshi.constants import CandlePeriod, MarketStatusFilter
from manta_trading.providers.errors import ProviderPermanentError

DESCRIPTION = "Record real Kalshi API responses as unit-test fixtures."
FIXTURE_DIR = Path("test/fixtures/kalshi")
PAGE_LIMIT = 5
#: Small category keeps the (unpaginated) series-list fixture a sane size.
SERIES_CATEGORY = "Health"
SERIES_TICKER = "FED"
UNKNOWN_TICKER = "NOPE-NOT-A-TICKER"
CANDLE_WINDOW = timedelta(hours=24)
CANDLE_TRADE_SCAN = 100
#: Up to this span, record 1-minute candles; beyond it, hourly.
MINUTE_CANDLE_MAX_SPAN = timedelta(hours=6)
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


RECORDERS: dict[str, Callable[[Recorder], Awaitable[None]]] = {
    "series_list": record_series_list,
    "series": record_series,
    "events": record_events,
    "markets": record_markets,
    "candlesticks": record_candlesticks,
    "trades": record_trades,
    "historical_cutoff": record_historical_cutoff,
    "error_404": record_error_404,
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
